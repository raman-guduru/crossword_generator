from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import tempfile
import subprocess
import json
from pathlib import Path
import time
import re

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend communication

# Configuration
UPLOAD_FOLDER = tempfile.mkdtemp()
ALLOWED_EXTENSIONS = {'txt'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'message': 'Hexagonal Crossword Generator API is running'})

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Upload and validate word list file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Only .txt files are allowed'}), 400
    
    try:
        # Save file temporarily
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Read and validate words
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            words = [line.strip().upper() for line in content.split('\n') if line.strip()]
        
        if not words:
            os.remove(filepath)
            return jsonify({'error': 'File contains no valid words'}), 400
        
        return jsonify({
            'success': True,
            'filename': filename,
            'filepath': filepath,
            'word_count': len(words),
            'words': words[:10],  # Return first 10 words as preview
            'longest_word': max(words, key=len),
            'longest_length': len(max(words, key=len))
        })
    
    except Exception as e:
        return jsonify({'error': f'Failed to process file: {str(e)}'}), 500

@app.route('/api/generate', methods=['POST'])
def generate_crossword():
    """Generate hexagonal crossword puzzle using hex_crossword.py"""
    try:
        data = request.json
        filepath = data.get('filepath')
        radius = int(data.get('radius', 3))  # Hex grid radius instead of grid_size
        min_quality = int(data.get('min_quality', 30))
        
        # Validate inputs
        if not filepath or not os.path.exists(filepath):
            return jsonify({'error': 'Invalid word file path'}), 400
        
        if radius < 2 or radius > 8:
            return jsonify({'error': 'Radius must be between 2 and 8'}), 400
        
        if min_quality < 0 or min_quality > 500:
            return jsonify({'error': 'Quality must be between 0 and 500'}), 400
        
        # Create output directory for this generation
        output_dir = tempfile.mkdtemp()
        cnf_output = os.path.join(output_dir, 'hex_crossword.cnf')
        
        # Find hex_crossword.py
        script_dir = os.path.dirname(os.path.abspath(__file__))
        hex_crossword_script = os.path.join(script_dir, 'hex_crossword.py')
        
        if not os.path.exists(hex_crossword_script):
            return jsonify({'error': 'hex_crossword.py not found in script directory'}), 500
        
        # Change to output directory so CNF file is created there
        original_dir = os.getcwd()
        os.chdir(output_dir)
        
        try:
            # Run hex_crossword.py with UTF-8 environment
            start_time = time.time()
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            
            # Command: python hex_crossword.py <word_file> <radius> <min_quality>
            process = subprocess.run(
                ['python', hex_crossword_script, filepath, str(radius), str(min_quality), 
                 '--timeout', '300', '--cnf', cnf_output],
                capture_output=True,
                text=True,
                timeout=360,  # 6 minute timeout (slightly more than solver timeout)
                env=env,
                encoding='utf-8',
                errors='replace'
            )
            end_time = time.time()
        finally:
            # Always change back to original directory
            os.chdir(original_dir)
        
        # Check if stdout/stderr are None
        stdout_text = process.stdout if process.stdout is not None else ""
        stderr_text = process.stderr if process.stderr is not None else ""
        
        # Parse output
        output_lines = stdout_text.split('\n') if stdout_text else []
        logs = []
        encoding_time = None
        cnf_time = None
        solving_time = None
        
        for line in output_lines:
            if line.strip():
                logs.append(line)
                # Extract timing information
                if 'Encoding' in line and '...' in line:
                    try:
                        time_match = re.search(r'([\d.]+)s', line)
                        if time_match:
                            encoding_time = float(time_match.group(1))
                    except:
                        pass
                elif 'CNF export' in line and '...' in line:
                    try:
                        time_match = re.search(r'([\d.]+)s', line)
                        if time_match:
                            cnf_time = float(time_match.group(1))
                    except:
                        pass
                elif 'Solving' in line and '...' in line:
                    try:
                        time_match = re.search(r'([\d.]+)s', line)
                        if time_match:
                            solving_time = float(time_match.group(1))
                    except:
                        pass
        
        # Add stderr to logs if present
        if stderr_text and stderr_text.strip():
            logs.append("=== STDERR ===")
            for line in stderr_text.split('\n'):
                if line.strip():
                    logs.append(line)
        
        # Check if successful
        if process.returncode != 0:
            return jsonify({
                'success': False,
                'error': f'Process failed with return code {process.returncode}. Check logs for details.',
                'logs': logs,
                'stderr': stderr_text
            })
        
        if 'unsatisfiable' in stdout_text.lower() or 'No solution exists' in stdout_text:
            return jsonify({
                'success': False,
                'error': 'No solution found. Try reducing the minimum quality or increasing radius.',
                'logs': logs,
                'stderr': stderr_text
            })
        
        # Parse the hexagonal solution
        placement_data = parse_hex_crossword_output(stdout_text, radius)
        
        if not placement_data or not placement_data.get('placements'):
            return jsonify({
                'success': False,
                'error': 'Failed to parse solution from output. The script may have encountered an error.',
                'logs': logs,
                'raw_output': stdout_text[:1000] if stdout_text else "No output"
            })
        
        # Check if CNF file was generated
        cnf_exists = os.path.exists(cnf_output)
        
        return jsonify({
            'success': True,
            'result': placement_data,
            'logs': logs,
            'timing': {
                'total': round(end_time - start_time, 2),
                'encoding': encoding_time,
                'cnf_export': cnf_time,
                'solving': solving_time
            },
            'cnf_available': cnf_exists,
            'cnf_path': cnf_output if cnf_exists else None
        })
    
    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'error': 'Generation timed out (>6 minutes). Try reducing radius or quality.',
            'logs': ['Timeout after 360 seconds']
        }), 408
    
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        return jsonify({
            'success': False,
            'error': f'Unexpected error: {str(e)}',
            'logs': [str(e), error_trace]
        }), 500

def parse_hex_crossword_output(output, radius):
    """Parse the hex_crossword.py output to extract placement and grid"""
    if not output:
        return None
    
    lines = output.split('\n')
    
    placements = []
    grid_display = []
    
    # Find placement information
    # Look for lines like: "1) WARCRAFT @ (0,0) orient=0"
    word_section_started = False
    for line in lines:
        # Check if we've reached the word placement section
        if 'Placed' in line and 'words' in line:
            word_section_started = True
            continue
        
        if word_section_started:
            # Try to match the placement pattern for hex grid
            # Format: "1) WORD @ (q,r) orient=0"
            match = re.search(r'\d+\)\s+(\w+)\s+@\s+\((-?\d+),(-?\d+)\)\s+orient=(\d+)', line)
            if match:
                word = match.group(1)
                q = int(match.group(2))
                r = int(match.group(3))
                orientation = int(match.group(4))
                
                placements.append({
                    'word': word,
                    'q': q,
                    'r': r,
                    'orientation': orientation
                })
    
    # Find the hexagonal grid display
    # Look for "--- Hexagonal Grid ---" section
    grid_start = -1
    grid_end = -1
    
    for i, line in enumerate(lines):
        if '--- Hexagonal Grid ---' in line:
            grid_start = i + 1
        elif '----------------------' in line and grid_start > 0:
            grid_end = i
            break
    
    # Extract grid display
    if grid_start >= 0 and grid_end >= 0:
        for i in range(grid_start, grid_end):
            grid_display.append(lines[i])
    
    total_length = sum(len(p['word']) for p in placements)
    
    return {
        'placements': placements,
        'grid_display': grid_display,
        'total_length': total_length,
        'word_count': len(placements),
        'radius': radius
    }

@app.route('/api/download-cnf', methods=['POST'])
def download_cnf():
    """Download the generated CNF file"""
    try:
        data = request.json
        cnf_path = data.get('cnf_path')
        
        if not cnf_path or not os.path.exists(cnf_path):
            return jsonify({'error': 'CNF file not found'}), 404
        
        return send_file(
            cnf_path,
            mimetype='text/plain',
            as_attachment=True,
            download_name='hex_crossword.cnf'
        )
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cleanup', methods=['POST'])
def cleanup():
    """Clean up temporary files"""
    try:
        data = request.json
        filepath = data.get('filepath')
        
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
        
        return jsonify({'success': True})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("="*50)
    print("Hexagonal Crossword Generator API Server")
    print("="*50)
    print(f"Script directory: {os.path.dirname(os.path.abspath(__file__))}")
    print(f"Upload folder: {UPLOAD_FOLDER}")
    print("Starting server on http://localhost:5000")
    print("="*50)
    app.run(debug=True, host='0.0.0.0', port=5000)