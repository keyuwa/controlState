#!/usr/bin/env python3
"""Web UI for model-assisted exploration.

Provides a browser interface to control and monitor the exploration process.
Displays real-time scenario generation, execution results, and findings.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_from_directory

from explore import explore, ROOT, EXPLORE_DIR
from model_client import ModelClient, ModelError

app = Flask(__name__)
app.config['SECRET_KEY'] = 'honeysat-explore-secret'

# Global state
exploration_state = {
    'running': False,
    'current_round': 0,
    'total_rounds': 0,
    'results': [],
    'error': None
}

exploration_thread = None


@app.route('/')
def index():
    return render_template('explore.html')


@app.route('/api/status')
def get_status():
    """Get current exploration status."""
    summary_file = EXPLORE_DIR / 'explore_summary.json'
    summary = {}
    if summary_file.exists():
        summary = json.loads(summary_file.read_text())

    return jsonify({
        'running': exploration_state['running'],
        'current_round': exploration_state['current_round'],
        'total_rounds': exploration_state['total_rounds'],
        'summary': summary,
        'error': exploration_state['error']
    })


@app.route('/api/config')
def get_config():
    """Get current model configuration."""
    return jsonify({
        'model_base_url': os.environ.get('MODEL_BASE_URL', ''),
        'model_name': os.environ.get('MODEL_NAME', ''),
        'yamcs_url': os.environ.get('YAMCS_URL', 'http://localhost:8090'),
        'yamcs_user': os.environ.get('YAMCS_USERNAME', 'administrator')
    })


@app.route('/api/rounds')
def get_rounds():
    """Get all round results."""
    rounds = []
    if EXPLORE_DIR.exists():
        for f in sorted(EXPLORE_DIR.glob('round_*.json')):
            try:
                data = json.loads(f.read_text())
                rounds.append({
                    'file': f.name,
                    'scenario_id': data.get('scenario', {}).get('scenario_id'),
                    'verdict': data.get('verdict', {}).get('verdict'),
                    'novel': data.get('novel', False),
                    'run_id': data.get('manifest_run_id')
                })
            except Exception as e:
                rounds.append({'file': f.name, 'error': str(e)})
    return jsonify({'rounds': rounds})


@app.route('/api/round/<int:round_num>')
def get_round_detail(round_num):
    """Get detailed info for a specific round."""
    round_file = EXPLORE_DIR / f'round_{round_num:03d}.json'
    if not round_file.exists():
        return jsonify({'error': 'Round not found'}), 404

    data = json.loads(round_file.read_text())
    return jsonify(data)


@app.route('/api/findings')
def get_findings():
    """Get all findings."""
    findings_dir = ROOT / 'findings'
    findings = []
    if findings_dir.exists():
        for d in findings_dir.iterdir():
            if d.is_dir():
                report = d / 'report.json'
                if report.exists():
                    findings.append(json.loads(report.read_text()))
    return jsonify({'findings': findings})


@app.route('/api/start', methods=['POST'])
def start_exploration():
    """Start a new exploration run."""
    global exploration_thread, exploration_state

    if exploration_state['running']:
        return jsonify({'error': 'Exploration already running'}), 400

    data = request.json or {}
    rounds = data.get('rounds', 5)
    budget = data.get('budget')

    # Validate environment
    try:
        client = ModelClient()
    except ModelError as e:
        return jsonify({'error': f'Model not configured: {e}'}), 400

    def run_exploration():
        global exploration_state
        exploration_state['running'] = True
        exploration_state['current_round'] = 0
        exploration_state['total_rounds'] = rounds
        exploration_state['error'] = None

        try:
            explore(rounds, budget)
        except Exception as e:
            exploration_state['error'] = str(e)
        finally:
            exploration_state['running'] = False

    exploration_thread = threading.Thread(target=run_exploration, daemon=True)
    exploration_thread.start()

    return jsonify({'status': 'started', 'rounds': rounds})


@app.route('/api/model_calls')
def get_model_calls():
    """Get model call history."""
    call_log = ROOT / 'model_calls.jsonl'
    calls = []
    if call_log.exists():
        for line in call_log.read_text().strip().split('\n'):
            if line:
                calls.append(json.loads(line))
    return jsonify({'calls': calls[-50:]})  # Last 50 calls


if __name__ == '__main__':
    EXPLORE_DIR.mkdir(exist_ok=True)
    port = 5001
    print("=" * 60)
    print("HoneySat 模型辅助探索 Web UI")
    print("=" * 60)
    print(f"访问地址: http://localhost:{port}")
    print(f"Yamcs 控制台: http://localhost:8090")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)
