from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
import sqlite3
import bcrypt
import os
from datetime import datetime
import json
from werkzeug.utils import secure_filename
import openai

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# OpenAI client setup
openai.api_key = os.environ.get('OPENAI_API_KEY', 'default_key')

def init_db():
    """Initialize the database with required tables"""
    conn = sqlite3.connect('studybuddy.db')
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            registration_number TEXT UNIQUE NOT NULL,
            program TEXT NOT NULL,
            year INTEGER NOT NULL,
            preferred_location TEXT,
            subjects TEXT,
            study_topics TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Study buddy requests table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS study_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            topic TEXT NOT NULL,
            location TEXT NOT NULL,
            description TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Notes table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            subject TEXT NOT NULL,
            description TEXT,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            uploaded_by INTEGER NOT NULL,
            downloads INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (uploaded_by) REFERENCES users (id)
        )
    ''')
    
    # Timetables table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS timetables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            schedule_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect('studybuddy.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register')
def register_page():
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    # Get study buddy requests
    study_requests = conn.execute('''
        SELECT sr.*, u.full_name, u.program, u.year 
        FROM study_requests sr 
        JOIN users u ON sr.user_id = u.id 
        WHERE sr.is_active = 1 AND sr.user_id != ?
        ORDER BY sr.created_at DESC
    ''', (session['user_id'],)).fetchall()
    
    # Get notes
    notes = conn.execute('''
        SELECT n.*, u.full_name as uploader_name 
        FROM notes n 
        JOIN users u ON n.uploaded_by = u.id 
        ORDER BY n.created_at DESC
    ''').fetchall()
    
    # Get user's timetable
    timetable = conn.execute('SELECT * FROM timetables WHERE user_id = ?', (session['user_id'],)).fetchone()
    
    conn.close()
    
    return render_template('dashboard.html', 
                         user=user, 
                         study_requests=study_requests, 
                         notes=notes, 
                         timetable=timetable)

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    
    # Hash password
    password_hash = bcrypt.hashpw(data['password'].encode('utf-8'), bcrypt.gensalt())
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (username, email, password, full_name, registration_number, 
                             program, year, preferred_location, subjects, study_topics)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data['username'], data['email'], password_hash, data['fullName'],
              data['registrationNumber'], data['program'], data['year'],
              data.get('preferredLocation', ''), json.dumps(data.get('subjects', [])),
              data.get('studyTopics', '')))
        
        user_id = cursor.lastrowid
        conn.commit()
        
        session['user_id'] = user_id
        session['username'] = data['username']
        
        return jsonify({'success': True, 'message': 'Registration successful'})
    
    except sqlite3.IntegrityError as e:
        return jsonify({'success': False, 'message': 'Username or email already exists'}), 400
    finally:
        conn.close()

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (data['username'],)).fetchone()
    conn.close()
    
    if user and bcrypt.checkpw(data['password'].encode('utf-8'), user['password']):
        session['user_id'] = user['id']
        session['username'] = user['username']
        return jsonify({'success': True, 'message': 'Login successful'})
    
    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/study-request', methods=['POST'])
def create_study_request():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    data = request.get_json()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO study_requests (user_id, subject, topic, location, description)
        VALUES (?, ?, ?, ?, ?)
    ''', (session['user_id'], data['subject'], data['topic'], 
          data['location'], data.get('description', '')))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Study request created'})

@app.route('/api/upload-note', methods=['POST'])
def upload_note():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'}), 400
    
    if file:
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
        unique_filename = timestamp + filename
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO notes (title, subject, description, filename, file_path, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (request.form['title'], request.form['subject'], 
              request.form.get('description', ''), filename, file_path, session['user_id']))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Note uploaded successfully'})

@app.route('/api/timetable', methods=['POST'])
def save_timetable():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    data = request.get_json()
    schedule_json = json.dumps(data['schedule'])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if timetable exists
    existing = cursor.execute('SELECT id FROM timetables WHERE user_id = ?', (session['user_id'],)).fetchone()
    
    if existing:
        cursor.execute('''
            UPDATE timetables SET schedule_data = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE user_id = ?
        ''', (schedule_json, session['user_id']))
    else:
        cursor.execute('''
            INSERT INTO timetables (user_id, schedule_data) VALUES (?, ?)
        ''', (session['user_id'], schedule_json))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Timetable saved'})

@app.route('/api/ai-chat', methods=['POST'])
def ai_chat():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    data = request.get_json()
    question = data.get('question', '')
    
    try:
        client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful AI study assistant for VIT Chennai engineering students. Provide clear, accurate, and educational responses."},
                {"role": "user", "content": question}
            ]
        )
        
        answer = response.choices[0].message.content
        return jsonify({'success': True, 'answer': answer})
    
    except Exception as e:
        return jsonify({'success': False, 'answer': 'I am experiencing technical difficulties. Please try again later.'})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
