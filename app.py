from flask import Flask, render_template, request, redirect, url_for, send_file, flash, jsonify
import sqlite3
import os
from werkzeug.utils import secure_filename
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
import base64
import io
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Tạo thư mục uploads nếu chưa có
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Khởi tạo database
def init_db():
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    
    # Bảng người ký
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            private_key TEXT NOT NULL,
            public_key TEXT NOT NULL,
            signature_image TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Bảng file đã ký
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signed_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_filename TEXT NOT NULL,
            signed_filename TEXT NOT NULL,
            signature_filename TEXT NOT NULL,
            signer_name TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            signature_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (signer_name) REFERENCES signers (name)
        )
    ''')
    
    conn.commit()
    conn.close()

# Tạo cặp khóa RSA
def generate_key_pair():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    public_key = private_key.public_key()
    
    # Chuyển đổi sang PEM format
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    return private_pem.decode('utf-8'), public_pem.decode('utf-8')

# Ký file
def sign_file(file_path, private_key_pem):
    try:
        # Đọc file
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        # Tạo hash SHA-256
        digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
        digest.update(file_data)
        file_hash = digest.finalize()
        
        # Load private key
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode('utf-8'),
            password=None,
            backend=default_backend()
        )
        
        # Ký hash
        signature = private_key.sign(
            file_hash,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        return signature, file_hash
    except Exception as e:
        print(f"Lỗi khi ký file: {str(e)}")
        return None, None

# Xác minh chữ ký
def verify_signature(file_path, signature_data, public_key_pem):
    try:
        # Đọc file
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        # Tạo hash SHA-256
        digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
        digest.update(file_data)
        file_hash = digest.finalize()
        
        # Load public key
        public_key = serialization.load_pem_public_key(
            public_key_pem.encode('utf-8'),
            backend=default_backend()
        )
        
        # Xác minh chữ ký
        public_key.verify(
            signature_data,
            file_hash,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return True
    except Exception as e:
        print(f"Lỗi xác minh: {str(e)}")
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name'].strip()
        signature_image = request.form.get('signature_image', '')
        
        if not name:
            flash('Vui lòng nhập tên!', 'error')
            return redirect(url_for('register'))
        
        # Tạo cặp khóa
        private_key, public_key = generate_key_pair()
        
        try:
            conn = sqlite3.connect('digital_signature.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO signers (name, private_key, public_key, signature_image)
                VALUES (?, ?, ?, ?)
            ''', (name, private_key, public_key, signature_image))
            conn.commit()
            conn.close()
            
            flash(f'Đăng ký thành công cho {name}!', 'success')
            return redirect(url_for('view_keys', name=name))
        except sqlite3.IntegrityError:
            flash('Tên này đã tồn tại!', 'error')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/keys/<name>')
def view_keys(name):
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM signers WHERE name = ?', (name,))
    signer = cursor.fetchone()
    conn.close()
    
    if not signer:
        flash('Không tìm thấy người ký!', 'error')
        return redirect(url_for('index'))
    
    return render_template('keys.html', signer=signer)

@app.route('/sign', methods=['GET', 'POST'])
def sign_file_route():
    if request.method == 'POST':
        signer_name = request.form['signer_name']
        
        if 'file' not in request.files:
            flash('Vui lòng chọn file!', 'error')
            return redirect(url_for('sign_file_route'))
        
        file = request.files['file']
        if file.filename == '':
            flash('Vui lòng chọn file!', 'error')
            return redirect(url_for('sign_file_route'))
        
        # Kiểm tra người ký có tồn tại không
        conn = sqlite3.connect('digital_signature.db')
        cursor = conn.cursor()
        cursor.execute('SELECT private_key FROM signers WHERE name = ?', (signer_name,))
        result = cursor.fetchone()
        
        if not result:
            flash('Người ký không tồn tại!', 'error')
            conn.close()
            return redirect(url_for('sign_file_route'))
        
        private_key = result[0]
        
        # Lưu file
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Ký file
        signature, file_hash = sign_file(file_path, private_key)
        
        if signature:
            # Lưu thông tin vào database
            signature_filename = f"{filename}.sig"
            signature_path = os.path.join(app.config['UPLOAD_FOLDER'], signature_filename)
            
            with open(signature_path, 'wb') as sig_file:
                sig_file.write(signature)
            
            cursor.execute('''
                INSERT INTO signed_files 
                (original_filename, signed_filename, signature_filename, signer_name, file_hash, signature_data)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (filename, filename, signature_filename, signer_name, 
                  base64.b64encode(file_hash).decode('utf-8'),
                  base64.b64encode(signature).decode('utf-8')))
            conn.commit()
            
            flash('Ký file thành công!', 'success')
        else:
            flash('Lỗi khi ký file!', 'error')
        
        conn.close()
        return redirect(url_for('signed_files'))
    
    # Lấy danh sách người ký
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM signers ORDER BY name')
    signers = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    return render_template('sign.html', signers=signers)

@app.route('/files')
def signed_files():
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT sf.*, s.public_key 
        FROM signed_files sf 
        JOIN signers s ON sf.signer_name = s.name 
        ORDER BY sf.created_at DESC
    ''')
    files = cursor.fetchall()
    conn.close()
    
    return render_template('files.html', files=files)

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if request.method == 'POST':
        if 'file' not in request.files or 'public_key' not in request.form:
            flash('Vui lòng cung cấp đầy đủ thông tin!', 'error')
            return redirect(url_for('verify'))
        
        file = request.files['file']
        public_key = request.form['public_key']
        signature_b64 = request.form.get('signature', '')
        
        if file.filename == '':
            flash('Vui lòng chọn file!', 'error')
            return redirect(url_for('verify'))
        
        try:
            signature_data = base64.b64decode(signature_b64)
        except:
            flash('Chữ ký không hợp lệ!', 'error')
            return redirect(url_for('verify'))
        
        # Lưu file tạm
        filename = secure_filename(file.filename)
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{filename}")
        file.save(temp_path)
        
        # Xác minh
        is_valid = verify_signature(temp_path, signature_data, public_key)
        
        # Xóa file tạm
        os.remove(temp_path)
        
        if is_valid:
            flash('Chữ ký hợp lệ!', 'success')
        else:
            flash('Chữ ký không hợp lệ!', 'error')
    
    # Lấy danh sách người ký để chọn
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, public_key FROM signers ORDER BY name')
    signers = cursor.fetchall()
    conn.close()
    
    return render_template('verify.html', signers=signers)

@app.route('/download/<file_type>/<filename>')
def download_file(file_type, filename):
    try:
        if file_type == 'original':
            return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename), as_attachment=True)
        elif file_type == 'signature':
            return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename), as_attachment=True)
        elif file_type == 'public_key':
            # Lấy public key từ tên file
            base_filename = filename.replace('.pub', '')
            conn = sqlite3.connect('digital_signature.db')
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.public_key 
                FROM signed_files sf 
                JOIN signers s ON sf.signer_name = s.name 
                WHERE sf.original_filename = ?
            ''', (base_filename,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                public_key = result[0]
                # Tạo file tạm cho public key
                temp_file = io.BytesIO(public_key.encode('utf-8'))
                return send_file(temp_file, as_attachment=True, 
                               download_name=f"{base_filename}.pub",
                               mimetype='text/plain')
    except Exception as e:
        flash(f'Lỗi tải file: {str(e)}', 'error')
    
    return redirect(url_for('signed_files'))

@app.route('/api/signer/<name>')
def get_signer_info(name):
    conn = sqlite3.connect('digital_signature.db')
    cursor = conn.cursor()  
    cursor.execute('SELECT public_key, signature_image FROM signers WHERE name = ?', (name,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return jsonify({
            'public_key': result[0],
            'signature_image': result[1] or ''
        })
    return jsonify({'error': 'Không tìm thấy người ký'})

if __name__ == '__main__':
    init_db()
    app.run(debug=True)