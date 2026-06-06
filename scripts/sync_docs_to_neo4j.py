#!/usr/bin/env python3
"""
Sync documentation from auto-assign, auto-router, and auto-assist repos to Neo4j.
Creates Documentation nodes with file content, metadata, and cross-references.
"""

import os
import re
from pathlib import Path
from neo4j import GraphDatabase

# Configuration
REPOS = {
    'auto-assign': '/home/scott/git/auto-assign',
    'auto-router': '/home/scott/git/auto-router', 
    'auto-assist': '/home/scott/git/auto-assist'
}

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "livelongandprosper"  # legacy phonelog app.py default


def find_md_files(repo_path):
    """Find all .md files in a repo (excluding venv, node_modules, etc.)"""
    md_files = []
    exclude_dirs = {'venv', '.venv', 'node_modules', '__pycache__', '.git'}
    
    for root, dirs, files in os.walk(repo_path):
        # Filter out excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if file.endswith('.md'):
                full_path = Path(root) / file
                md_files.append(full_path)
    
    return md_files


def extract_metadata(content, filename):
    """Extract title and sections from markdown content"""
    metadata = {
        'title': filename.replace('.md', '').replace('-', ' ').title(),
        'sections': [],
        'code_blocks': 0,
        'word_count': len(content.split())
    }
    
    # Extract title (first H1)
    h1_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if h1_match:
        metadata['title'] = h1_match.group(1).strip()
    
    # Extract sections (H2, H3 headers)
    sections = re.findall(r'^(#{2,3})\s+(.+)$', content, re.MULTILINE)
    for level, title in sections:
        metadata['sections'].append({
            'level': len(level),
            'title': title.strip()
        })
    
    # Count code blocks
    metadata['code_blocks'] = len(re.findall(r'```', content)) // 2
    
    return metadata


def sync_to_neo4j():
    """Sync all documentation to Neo4j"""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    try:
        with driver.session() as session:
            total_files = 0
            
            for repo_name, repo_path in REPOS.items():
                print(f"\n📚 Processing {repo_name}...")
                
                md_files = find_md_files(repo_path)
                print(f"   Found {len(md_files)} .md files")
                
                for file_path in md_files:
                    try:
                        # Read content
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()[:50000]  # Limit to 50k chars
                        
                        # Extract metadata
                        filename = file_path.name
                        metadata = extract_metadata(content, filename)
                        
                        # Create Neo4j node
                        rel_path = file_path.relative_to(repo_path)
                        
                        session.run("""
                            MERGE (doc:Documentation {repo: $repo, path: $path})
                            SET doc.title = $title,
                                doc.content = $content,
                                doc.sections = $sections,
                                doc.code_blocks = $code_blocks,
                                doc.word_count = $word_count,
                                doc.last_synced = datetime()
                            RETURN doc
                        """,
                            repo=repo_name,
                            path=str(rel_path),
                            title=metadata['title'],
                            content=content,
                            sections=metadata['sections'],
                            code_blocks=metadata['code_blocks'],
                            word_count=metadata['word_count']
                        )
                        
                        # Index key concepts for search
                        session.run("""
                            MATCH (doc:Documentation {repo: $repo, path: $path})
                            CALL apoc.create.addLabels(doc, ['Doc_' + toUpper(split($title, ' ')[0])]) YIELD node
                            RETURN node
                        """,
                            repo=repo_name,
                            path=str(rel_path),
                            title=metadata['title']
                        )
                        
                        total_files += 1
                        
                    except Exception as e:
                        print(f"   ⚠️ Error processing {file_path}: {e}")
                
                print(f"   ✅ Synced {repo_name} documentation")
            
            # Create relationships between docs in same repo
            session.run("""
                MATCH (a:Documentation), (b:Documentation)
                WHERE a.repo = b.repo AND a.path < b.path
                MERGE (a)-[:RELATED_TO]->(b)
                RETURN count(*) as relationships_created
            """)
            
            print(f"\n🎉 Sync complete! Total files indexed: {total_files}")
            
    except Exception as e:
        print(f"❌ Error syncing to Neo4j: {e}")
    finally:
        driver.close()


def check_deployment_status():
    """Check deployment status of all three services"""
    import subprocess
    
    print("\n🔍 Checking Deployment Status...")
    print("=" * 60)
    
    services = [
        ('auto-assist-api', '8000', 'FastAPI'),
        ('auto-assist-worker', '8000', 'Worker'),
        ('auto-assist-redis', '6379', 'Redis'),
        ('auto-assist-ollama', '11434', 'Ollama'),
        ('auto-router', '8088', 'Router'),
        ('auto-router-redis', '6379', 'Redis'),
    ]
    
    for service, port, type_ in services:
        try:
            result = subprocess.run(
                f'docker ps --filter "name={service}" --format "{{{{.Status}}}}"',
                shell=True, capture_output=True, text=True, timeout=3
            )
            
            if result.stdout.strip():
                status = result.stdout.strip()
                if 'healthy' in status.lower():
                    print(f"✅ {service:25s} - Running (healthy) on port {port}")
                elif 'up' in status.lower():
                    print(f"⚠️  {service:25s} - Running on port {port}")
                else:
                    print(f"❌ {service:25s} - {status[:50]}")
            else:
                print(f"❌ {service:25s} - Not running")
                
        except Exception as e:
            print(f"❌ {service:25s} - Error checking: {e}")


def verify_integration_endpoints():
    """Verify integration endpoints are responding"""
    import requests
    
    print("\n🔗 Verifying Integration Endpoints...")
    print("=" * 60)
    
    endpoints = [
        ('http://localhost:8000/health', 'auto-assist API'),
        ('http://localhost:8088/health', 'auto-router API'),
        ('http://localhost:8088/v1/models', 'auto-router models'),
        ('http://localhost:8088/admin/context', 'auto-router context'),
    ]
    
    for url, name in endpoints:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"✅ {name:30s} - {url}")
            else:
                print(f"⚠️  {name:30s} - Status {response.status_code}: {url}")
        except Exception as e:
            print(f"❌ {name:30s} - Error: {e}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Sync docs to Neo4j and check deployment')
    parser.add_argument('--sync', action='store_true', help='Run documentation sync')
    parser.add_argument('--status', action='store_true', help='Check deployment status')
    parser.add_argument('--verify', action='store_true', help='Verify integration endpoints')
    parser.add_argument('--all', action='store_true', help='Run all checks')
    
    args = parser.parse_args()
    
    if args.all or args.sync:
        sync_to_neo4j()
    
    if args.all or args.status:
        check_deployment_status()
    
    if args.all or args.verify:
        verify_integration_endpoints()
