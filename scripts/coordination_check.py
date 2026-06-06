#!/usr/bin/env python3
"""
Delegation coordination script for multi-agent work across repos.
Designed to be used with Hermes Agent's delegate_task tool or cronjob.
"""

import subprocess
import json
from datetime import datetime, timedelta


def check_system_health():
    """Check health of all services and return status dict"""
    import requests
    
    health = {
        'timestamp': datetime.utcnow().isoformat(),
        'services': {},
        'integration': {},
        'neo4j_docs_count': 0,
        'issues': []
    }
    
    # Check auto-assist API
    try:
        resp = requests.get('http://localhost:8000/health', timeout=5)
        health['services']['auto-assist-api'] = {
            'status': 'healthy' if resp.status_code == 200 else 'unhealthy',
            'port': 8000,
            'response': resp.json() if resp.headers.get('content-type') == 'application/json' else str(resp.text)[:200]
        }
    except Exception as e:
        health['services']['auto-assist-api'] = {'status': 'down', 'error': str(e)}
        health['issues'].append('auto-assist-api down')
    
    # Check auto-router
    try:
        resp = requests.get('http://localhost:8088/health', timeout=5)
        health['services']['auto-router'] = {
            'status': 'healthy' if resp.status_code == 200 else 'unhealthy',
            'port': 8088,
            'response': resp.json() if resp.headers.get('content-type') == 'application/json' else str(resp.text)[:200]
        }
    except Exception as e:
        health['services']['auto-router'] = {'status': 'down', 'error': str(e)}
        health['issues'].append('auto-router down')
    
    # Check integration endpoints
    try:
        resp = requests.get('http://localhost:8088/v1/models', timeout=5)
        health['integration']['models_endpoint'] = {
            'status': 'ok' if resp.status_code == 200 else 'error',
            'model_count': len(resp.json().get('data', [])) if resp.headers.get('content-type') == 'application/json' else 0
        }
    except Exception as e:
        health['integration']['models_endpoint'] = {'status': 'error', 'error': str(e)}
    
    try:
        resp = requests.get('http://localhost:8088/admin/context', timeout=5)
        health['integration']['context_endpoint'] = {
            'status': 'ok' if resp.status_code == 200 else 'error',
            'has_context': bool(resp.json()) if resp.headers.get('content-type') == 'application/json' else False
        }
    except Exception as e:
        health['integration']['context_endpoint'] = {'status': 'error', 'error': str(e)}
    
    return health


def sync_docs_to_neo4j():
    """Run the documentation sync script"""
    result = subprocess.run(
        ['python3', '/home/scott/git/auto-assist/scripts/sync_docs_to_neo4j.py', '--sync'],
        capture_output=True, text=True, timeout=120
    )
    
    return {
        'success': result.returncode == 0,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'timestamp': datetime.utcnow().isoformat()
    }


def get_neo4j_doc_count():
    """Query Neo4j for documentation count"""
    from neo4j import GraphDatabase
    
    driver = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'livelongandprosper'))
    
    try:
        with driver.session() as session:
            result = session.run("MATCH (d:Documentation) RETURN count(d) as count")
            record = result.single()
            return record['count'] if record else 0
    except Exception as e:
        print(f"Neo4j query error: {e}")
        return -1


def generate_coordination_report():
    """Generate a comprehensive coordination report"""
    health = check_system_health()
    
    # Get Neo4j doc count
    try:
        health['neo4j_docs_count'] = get_neo4j_doc_count()
    except Exception as e:
        health['neo4j_docs_count'] = -1
        health['issues'].append(f"Neo4j query failed: {e}")
    
    # Generate report
    report = f"""
# Coordination Report - {health['timestamp']}

## Service Health

| Service | Status | Port |
|---------|--------|------|
"""
    
    for service, data in health['services'].items():
        status_icon = '✅' if data.get('status') == 'healthy' else '❌'
        report += f"| {service} | {status_icon} {data.get('status', 'unknown')} | {data.get('port', '?')} |\n"
    
    # Integration status
    report += "\n## Integration Endpoints\n\n"
    for endpoint, data in health['integration'].items():
        status_icon = '✅' if data.get('status') == 'ok' else '⚠️'
        report += f"- {status_icon} {endpoint}: {data.get('status', 'unknown')}\n"
    
    # Neo4j sync status
    report += f"\n## Documentation Sync\n\n"
    report += f"- **Indexed docs in Neo4j**: {health['neo4j_docs_count']}\n"
    
    # Issues
    if health['issues']:
        report += "\n## Known Issues\n\n"
        for issue in health['issues']:
            report += f"- ⚠️  {issue}\n"
    
    return report


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Coordination script for multi-agent repos')
    parser.add_argument('--health', action='store_true', help='Check system health')
    parser.add_argument('--sync-docs', action='store_true', help='Sync docs to Neo4j')
    parser.add_argument('--report', action='store_true', help='Generate coordination report')
    parser.add_argument('--all', action='store_true', help='Run all checks')
    
    args = parser.parse_args()
    
    if args.all or args.health:
        health = check_system_health()
        print(json.dumps(health, indent=2))
    
    if args.all or args.sync_docs:
        result = sync_docs_to_neo4j()
        print("=== DOC SYNC RESULT ===")
        print(result['stdout'])
        if result['stderr']:
            print(result['stderr'])
    
    if args.all or args.report:
        report = generate_coordination_report()
        print(report)


if __name__ == '__main__':
    main()
