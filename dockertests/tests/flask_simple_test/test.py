"""
Simple Flask server test - minimal version to test server-side tracing.

Just starts Flask, handles one request, and exits.
"""
from flask import Flask, jsonify
import sys

app = Flask(__name__)

request_count = 0

@app.route('/test')
def test_endpoint():
    """Simple test endpoint."""
    global request_count
    request_count += 1
    return jsonify({'message': 'Hello!', 'count': request_count})

if __name__ == '__main__':
    print("Starting Flask server...")
    # Run for a short time and exit
    from threading import Timer
    def shutdown():
        print(f"Shutting down after {request_count} requests")
        sys.exit(0 if request_count > 0 else 1)
    
    # Shutdown after 5 seconds
    Timer(5.0, shutdown).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
