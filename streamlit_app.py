"""
Elasticsearch Cluster Configuration Generator
===========================================

A comprehensive tool for generating production-ready Elasticsearch clusters with Docker Compose.

Features:
- 3 node types: Master-only, Master+Data+Ingest, Data+Ingest only  
- Automatic optimization based on hardware specs
- Split-brain prevention with proper master node calculation
- 50+ production-ready settings auto-configured
- Role-specific tuning and capacity estimates

UI Enhancement Opportunity:
--------------------------
This app can be enhanced with streamlit-shadcn-ui for modern UI components:

Installation:
pip install streamlit-shadcn-ui

Usage Examples:
import streamlit_shadcn_ui as ui

# Modern button
ui.button("Generate Config", key="gen_btn", variant="default")

# Enhanced cards  
ui.card(content="Node Configuration", key="node_card")

# Better metrics display
ui.metric(label="Heap Size", value="16GB", delta="50% of RAM")

# Sleek badges for node roles
ui.badge(text="Master", variant="secondary") 

See: https://github.com/ObservedObserver/streamlit-shadcn-ui
"""

import streamlit as st
import json
import yaml
from datetime import datetime
import zipfile
import io
from typing import Dict, List, Any

# TODO: Uncomment after installing streamlit-shadcn-ui
# import streamlit_shadcn_ui as ui

# Configure Streamlit page
st.set_page_config(
    page_title="Elasticsearch Cluster Generator",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Initialize session state
def ensure_nodes_sync():
    """Ensure nodes list matches the node count"""
    current_nodes = len(st.session_state.cluster_config['nodes'])
    target_count = st.session_state.node_count
    
    if current_nodes < target_count:
        # Add missing nodes
        for i in range(current_nodes, target_count):
            new_node = {
                'name': generate_node_name(i + 1, st.session_state.cluster_config['primary_domain']),
                'hostname': generate_hostname(i + 1, st.session_state.cluster_config['primary_domain']),
                'roles': ['master', 'data', 'ingest'],
                'cpu_cores': 8,
                'ram_gb': 32,
                'ip': f"10.0.1.{10 + i}",
                'http_port': 9200,
                'transport_port': 9300
            }
            st.session_state.cluster_config['nodes'].append(new_node)
    elif current_nodes > target_count:
        # Remove excess nodes
        st.session_state.cluster_config['nodes'] = st.session_state.cluster_config['nodes'][:target_count]

def init_session_state():
    if 'cluster_config' not in st.session_state:
        st.session_state.cluster_config = {
            'es_version': '7.17.22',
            'primary_domain': 'example.com',
            'cluster_name': 'production-cluster',
            'nodes': [],
            'xpack_settings': {
                'security': False,
                'monitoring': False,
                'ml': False,
                'watcher': False,
                'graph': False,
                'ilm': True
            }
        }
    
    if 'node_count' not in st.session_state:
        st.session_state.node_count = 3
    
    # Ensure nodes are always in sync with node_count
    ensure_nodes_sync()

# Available Elasticsearch versions
ES_VERSIONS = [
    '8.15.0', '8.14.3', '8.13.4', '8.12.2', '8.11.4',
    '7.17.22', '7.16.3', '7.15.2', '7.14.2', '7.13.4',
    '6.8.23', '6.8.21'
]

# Node role options
NODE_ROLES = {
    'master_only': {
        'label': 'Master Only',
        'roles': ['master'],
        'description': 'Dedicated master node (recommended for large clusters, lightweight)'
    },
    'master_data_ingest': {
        'label': 'Master + Data + Ingest',
        'roles': ['master', 'data', 'ingest'],
        'description': 'Full-featured node (recommended for small-medium clusters)'
    },
    'data_ingest': {
        'label': 'Data + Ingest Only',
        'roles': ['data', 'ingest'],
        'description': 'Data node without master eligibility (for large clusters)'
    }
}

def calculate_optimal_settings(cpu_cores: int, ram_gb: int, node_roles: list):
    """Calculate optimal Elasticsearch settings based on hardware"""
    # Calculate heap size (50% of RAM, max 31GB for compressed OOPs)
    heap_gb = min(ram_gb // 2, 31)
    
    # Calculate thread pool sizes based on CPU cores and node roles
    is_master_only = node_roles == ['master']
    is_data_node = 'data' in node_roles
    
    # For master-only nodes, use lighter thread pools
    if is_master_only:
        thread_pools = {
            'search': max(cpu_cores // 2, 1),
            'index': max(cpu_cores // 4, 1),
            'bulk': max(cpu_cores // 4, 1),
            'write': max(cpu_cores // 4, 1),
            'get': max(cpu_cores // 2, 1)
        }
    else:
        # For data nodes, use full thread pools
        thread_pools = {
            'search': max(int(cpu_cores * 1.5), 1),      # 1.5x cores for search-heavy workloads
            'index': max(cpu_cores // 2, 1),             # 0.5x cores for indexing
            'bulk': max(cpu_cores // 2, 1),              # 0.5x cores for bulk operations
            'write': max(cpu_cores // 2, 1),             # 0.5x cores for single writes
            'get': cpu_cores                              # 1x cores for get operations
        }
    
    # Memory settings based on node type
    if is_master_only:
        memory_settings = {
            'index_buffer_size': '10%',      # Lower for master-only
            'total_breaker_limit': '60%',    # Conservative for master
            'request_breaker_limit': '50%',  # Lower request limit
            'fielddata_breaker_limit': '30%' # Lower fielddata limit
        }
        cache_settings = {
            'queries_cache_size': '5%',      # Minimal query cache
            'fielddata_cache_size': '10%',   # Minimal fielddata cache
            'requests_cache_size': '2%'      # Minimal request cache
        }
    else:
        memory_settings = {
            'index_buffer_size': '25%',      # Higher for data nodes
            'total_breaker_limit': '70%',    # Standard for data nodes
            'request_breaker_limit': '60%',  # Standard request limit
            'fielddata_breaker_limit': '40%' # Standard fielddata limit
        }
        cache_settings = {
            'queries_cache_size': '15%',     # Standard query cache
            'fielddata_cache_size': '20%',   # Standard fielddata cache
            'requests_cache_size': '5%'      # Standard request cache
        }
    
    # Recovery and rebalancing settings based on cluster size and node type
    recovery_settings = {
        'max_bytes_per_sec': '400mb',
        'node_concurrent_recoveries': 3,
        'node_concurrent_incoming_recoveries': 3,
        'node_concurrent_outgoing_recoveries': 3,
        'cluster_concurrent_rebalance': 4
    }
    
    # Network and transport settings
    network_settings = {
        'tcp_keep_alive': True,
        'tcp_reuse_address': True,
        'tcp_connect_timeout': '30s',
        'publish_timeout': '60s',
        'fd_ping_timeout': '30s',
        'fd_ping_retries': 3
    }
    
    # Advanced JVM settings based on heap size
    if heap_gb <= 8:
        jvm_settings = {
            'gc_collector': 'ConcMarkSweep',
            'cms_initiating_occupancy_fraction': 75,
            'additional_opts': [
                '-XX:+UseCMSInitiatingOccupancyOnly',
                '-XX:+CMSParallelRemarkEnabled',
                '-XX:+UseCMSCompactAtFullCollection'
            ]
        }
    else:
        jvm_settings = {
            'gc_collector': 'G1GC',
            'g1_heap_region_size': '16m',
            'additional_opts': [
                '-XX:+UseG1GC',
                '-XX:G1HeapRegionSize=16m',
                '-XX:+G1PrintRegionRememberSetInfo'
            ]
        }
    
    # Cluster settings based on node roles
    cluster_settings = {
        'max_shards_per_node': 1500,
        'total_shards_per_node': 1500,
        'action_destructive_requires_name': True
    }
    
    # Performance monitoring settings
    monitoring_settings = {
        'slow_query_threshold_warn': '1s',
        'slow_query_threshold_info': '500ms',
        'slow_fetch_threshold_warn': '1s',
        'slow_index_threshold_warn': '1s'
    }
    
    return {
        'heap_size': f"{heap_gb}g",
        'thread_pools': thread_pools,
        'memory_settings': memory_settings,
        'cache_settings': cache_settings,
        'recovery_settings': recovery_settings,
        'network_settings': network_settings,
        'jvm_settings': jvm_settings,
        'cluster_settings': cluster_settings,
        'monitoring_settings': monitoring_settings,
        'capacity_estimates': {
            'data_capacity_gb': heap_gb * 30,  # 1:30 ratio heap to data
            'concurrent_searches': thread_pools['search'] * 100,  # Estimate based on search threads
            'indexing_rate_docs_per_sec': thread_pools['bulk'] * 1000,  # Estimate based on bulk threads
            'is_master_only': is_master_only,
            'is_data_node': is_data_node
        }
    }

def calculate_minimum_master_nodes(total_nodes: int, master_eligible_count: int) -> int:
    """Calculate minimum master nodes to avoid split-brain"""
    return (master_eligible_count // 2) + 1

def generate_node_name(node_index: int, domain: str) -> str:
    """Generate default node name"""
    return f"els{node_index:02d}"

def generate_hostname(node_index: int, domain: str) -> str:
    """Generate default hostname"""
    return f"els{node_index:02d}.{domain}"

# Initialize session state after function definitions
init_session_state()

def save_configuration() -> bytes:
    """Save current configuration to JSON with comprehensive data"""
    
    # Calculate summary statistics
    master_eligible = [n for n in st.session_state.cluster_config['nodes'] if 'master' in n['roles']]
    data_nodes = [n for n in st.session_state.cluster_config['nodes'] if 'data' in n['roles']]
    total_cpu = sum(node['cpu_cores'] for node in st.session_state.cluster_config['nodes'])
    total_ram = sum(node['ram_gb'] for node in st.session_state.cluster_config['nodes'])
    
    config_data = {
        'version': '2.0',
        'generated_at': datetime.now().isoformat(),
        'generator': 'Elasticsearch Cluster Configuration Generator',
        
        # Complete cluster configuration
        'cluster_config': st.session_state.cluster_config,
        'node_count': st.session_state.node_count,
        
        # Summary statistics for reference
        'cluster_summary': {
            'total_nodes': len(st.session_state.cluster_config['nodes']),
            'master_eligible_nodes': len(master_eligible),
            'data_nodes': len(data_nodes),
            'total_cpu_cores': total_cpu,
            'total_ram_gb': total_ram,
            'minimum_master_nodes': calculate_minimum_master_nodes(len(st.session_state.cluster_config['nodes']), len(master_eligible)) if master_eligible else 0,
            'elasticsearch_version': st.session_state.cluster_config['es_version'],
            'cluster_name': st.session_state.cluster_config['cluster_name'],
            'primary_domain': st.session_state.cluster_config['primary_domain']
        },
        
        # Node breakdown for reference
        'node_breakdown': {
            'master_only': [n['name'] for n in st.session_state.cluster_config['nodes'] if n['roles'] == ['master']],
            'master_data_ingest': [n['name'] for n in st.session_state.cluster_config['nodes'] if set(n['roles']) == {'master', 'data', 'ingest'}],
            'data_ingest_only': [n['name'] for n in st.session_state.cluster_config['nodes'] if set(n['roles']) == {'data', 'ingest'}]
        },
        
        # X-Pack configuration
        'xpack_features': st.session_state.cluster_config['xpack_settings']
    }
    
    return json.dumps(config_data, indent=2).encode('utf-8')

def load_configuration(uploaded_file) -> bool:
    """Load configuration from uploaded JSON file - supports v1.0 and v2.0 formats"""
    try:
        config_data = json.loads(uploaded_file.getvalue().decode('utf-8'))
        
        # Check for required fields
        if 'cluster_config' not in config_data or 'node_count' not in config_data:
            st.error("❌ Invalid configuration file: Missing required fields")
            return False
        
        # Load core configuration
        st.session_state.cluster_config = config_data['cluster_config']
        st.session_state.node_count = config_data['node_count']
        
        # Validate loaded data
        if not isinstance(st.session_state.cluster_config.get('nodes', []), list):
            st.error("❌ Invalid configuration file: Invalid nodes data")
            return False
        
        if len(st.session_state.cluster_config['nodes']) != st.session_state.node_count:
            st.error("❌ Configuration mismatch: Node count doesn't match nodes array")
            return False
        
        # Ensure all required fields exist in cluster_config
        required_fields = ['es_version', 'primary_domain', 'cluster_name', 'nodes', 'xpack_settings']
        for field in required_fields:
            if field not in st.session_state.cluster_config:
                st.error(f"❌ Invalid configuration file: Missing {field}")
                return False
        
        # Validate node structure
        for i, node in enumerate(st.session_state.cluster_config['nodes']):
            required_node_fields = ['name', 'hostname', 'roles', 'cpu_cores', 'ram_gb', 'ip', 'http_port', 'transport_port']
            for field in required_node_fields:
                if field not in node:
                    st.error(f"❌ Invalid configuration file: Node {i+1} missing {field}")
                    return False
        
        return True
        
    except json.JSONDecodeError:
        st.error("❌ Invalid JSON file format")
        return False
    except Exception as e:
        st.error(f"❌ Error loading configuration: {str(e)}")
        return False

def generate_individual_docker_compose(node, config):
    """Generate individual Docker Compose file for a single node"""
    cluster_name = config['cluster_name']
    es_version = config['es_version']
    nodes = config['nodes']
    
    # Generate discovery hosts
    discovery_hosts = ','.join([n['hostname'] for n in nodes])
    
    # Calculate minimum master nodes
    master_eligible = [n for n in nodes if 'master' in n['roles']]
    min_master_nodes = calculate_minimum_master_nodes(len(nodes), len(master_eligible))
    
    optimal_settings = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
    
    # X-Pack settings
    xpack_env = []
    xpack_settings = config['xpack_settings']
    for setting, enabled in xpack_settings.items():
        if setting == 'ilm':
            xpack_env.append(f"      - xpack.ilm.enabled={'true' if enabled else 'false'}")
        else:
            xpack_env.append(f"      - xpack.{setting}.enabled={'true' if enabled else 'false'}")
    
    # Node roles
    master_eligible_flag = 'true' if 'master' in node['roles'] else 'false'
    data_node = 'true' if 'data' in node['roles'] else 'false'
    ingest_node = 'true' if 'ingest' in node['roles'] else 'false'
    
    # JVM options based on optimal settings
    if optimal_settings['jvm_settings']['gc_collector'] == 'G1GC':
        jvm_opts = f"-Xms{optimal_settings['heap_size']} -Xmx{optimal_settings['heap_size']} " + \
                  " ".join(optimal_settings['jvm_settings']['additional_opts'])
    else:
        jvm_opts = f"-Xms{optimal_settings['heap_size']} -Xmx{optimal_settings['heap_size']} " + \
                  f"-XX:+UseConcMarkSweepGC -XX:CMSInitiatingOccupancyFraction={optimal_settings['jvm_settings']['cms_initiating_occupancy_fraction']} " + \
                  " ".join(optimal_settings['jvm_settings']['additional_opts'])
    
    # Generate extra_hosts entries for all nodes in cluster
    extra_hosts_list = []
    for cluster_node in nodes:
        if cluster_node['name'] != node['name']:  # Don't add self
            extra_hosts_list.append(f"      - \"{cluster_node['hostname']}:{cluster_node['ip']}\"")
    
    extra_hosts_content = "\n".join(extra_hosts_list)
    
    compose_content = f"""# Docker Compose for {node['name']} - {cluster_name}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Node Type: {', '.join(node['roles']).title()}
# Extra Hosts: {len(extra_hosts_list)} other cluster nodes

version: '3.8'

services:
  {node['name']}:
    image: docker.elastic.co/elasticsearch/elasticsearch:{es_version}
    container_name: {node['name']}
    hostname: {node['hostname']}
    environment:
      # ==================== CLUSTER CONFIGURATION ====================
      - cluster.name={cluster_name}
      - node.name={node['name']}
      
      # ==================== NODE ROLES ====================
      - node.master={master_eligible_flag}
      - node.data={data_node}
      - node.ingest={ingest_node}
      
      # ==================== MEMORY OPTIMIZATION ====================
      - "ES_JAVA_OPTS={jvm_opts}"
      - bootstrap.memory_lock=true
      
      # ==================== DISCOVERY CONFIGURATION ====================
      - discovery.zen.minimum_master_nodes={min_master_nodes}
      - discovery.zen.ping.unicast.hosts={discovery_hosts}
      - discovery.zen.fd.ping_timeout={optimal_settings['network_settings']['fd_ping_timeout']}
      - discovery.zen.fd.ping_retries={optimal_settings['network_settings']['fd_ping_retries']}
      - discovery.zen.publish_timeout={optimal_settings['network_settings']['publish_timeout']}
      
      # ==================== PERFORMANCE OPTIMIZATION ====================
      # Memory Management
      - indices.memory.index_buffer_size={optimal_settings['memory_settings']['index_buffer_size']}
      - indices.memory.min_index_buffer_size=128mb
      - indices.breaker.total.limit={optimal_settings['memory_settings']['total_breaker_limit']}
      - indices.breaker.request.limit={optimal_settings['memory_settings']['request_breaker_limit']}
      - indices.breaker.fielddata.limit={optimal_settings['memory_settings']['fielddata_breaker_limit']}
      
      # Cache Configuration ({node['ram_gb']}GB RAM optimized)
      - indices.queries.cache.size={optimal_settings['cache_settings']['queries_cache_size']}
      - indices.fielddata.cache.size={optimal_settings['cache_settings']['fielddata_cache_size']}
      - indices.requests.cache.size={optimal_settings['cache_settings']['requests_cache_size']}
      
      # Thread Pool Configuration ({node['cpu_cores']}-core optimized, {', '.join(node['roles'])} node)
      - thread_pool.search.size={optimal_settings['thread_pools']['search']}
      - thread_pool.search.queue_size=2000
      - thread_pool.index.size={optimal_settings['thread_pools']['index']}
      - thread_pool.index.queue_size=1000
      - thread_pool.bulk.size={optimal_settings['thread_pools']['bulk']}
      - thread_pool.bulk.queue_size=2000
      - thread_pool.write.size={optimal_settings['thread_pools']['write']}
      - thread_pool.write.queue_size=1000
      - thread_pool.get.size={optimal_settings['thread_pools']['get']}
      - thread_pool.get.queue_size=1000
      
      # ==================== RECOVERY & REBALANCING ====================
      - indices.recovery.max_bytes_per_sec={optimal_settings['recovery_settings']['max_bytes_per_sec']}
      - cluster.routing.allocation.node_concurrent_recoveries={optimal_settings['recovery_settings']['node_concurrent_recoveries']}
      - cluster.routing.allocation.node_concurrent_incoming_recoveries={optimal_settings['recovery_settings']['node_concurrent_incoming_recoveries']}
      - cluster.routing.allocation.node_concurrent_outgoing_recoveries={optimal_settings['recovery_settings']['node_concurrent_outgoing_recoveries']}
      - cluster.routing.allocation.cluster_concurrent_rebalance={optimal_settings['recovery_settings']['cluster_concurrent_rebalance']}
      
      # ==================== CLUSTER LIMITS & SAFETY ====================
      - cluster.max_shards_per_node={optimal_settings['cluster_settings']['max_shards_per_node']}
      - cluster.routing.allocation.total_shards_per_node={optimal_settings['cluster_settings']['total_shards_per_node']}
      - action.destructive_requires_name={'true' if optimal_settings['cluster_settings']['action_destructive_requires_name'] else 'false'}
      
      # ==================== PERFORMANCE MONITORING ====================
      - logger.index.search.slowlog.threshold.query.warn={optimal_settings['monitoring_settings']['slow_query_threshold_warn']}
      - logger.index.search.slowlog.threshold.query.info={optimal_settings['monitoring_settings']['slow_query_threshold_info']}
      - logger.index.search.slowlog.threshold.fetch.warn={optimal_settings['monitoring_settings']['slow_fetch_threshold_warn']}
      - logger.index.indexing.slowlog.threshold.index.warn={optimal_settings['monitoring_settings']['slow_index_threshold_warn']}
      
      # ==================== X-PACK FEATURES ====================
{chr(10).join(xpack_env)}
      
      # ==================== NETWORK CONFIGURATION ====================
      - network.host=0.0.0.0
      - network.publish_host={node['ip']}
      - transport.tcp.port=9300
      - http.port=9200
      - http.cors.enabled=true
      - http.cors.allow-origin="*"
      - transport.tcp.keep_alive={'true' if optimal_settings['network_settings']['tcp_keep_alive'] else 'false'}
      - transport.tcp.reuse_address={'true' if optimal_settings['network_settings']['tcp_reuse_address'] else 'false'}
      - transport.tcp.connect_timeout={optimal_settings['network_settings']['tcp_connect_timeout']}
      
    ports:
      - "{node['http_port']}:9200"
      - "{node['transport_port']}:9300"
      
    volumes:
      - ./{node['name']}/data:/usr/share/elasticsearch/data
      - ./{node['name']}/logs:/usr/share/elasticsearch/logs
      - ./{node['name']}/backups:/usr/share/elasticsearch/backups
      - ./{node['name']}/config:/usr/share/elasticsearch/config
      
    extra_hosts:
{extra_hosts_content}
      
    networks:
      - elasticsearch-net
      
    ulimits:
      memlock:
        soft: -1
        hard: -1
      nofile:
        soft: 65536
        hard: 65536
      
    # ==================== RESOURCE LIMITS ====================
    mem_limit: {node['ram_gb']}g
    cpus: '{node['cpu_cores']}.0'
    
    restart: unless-stopped
    
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:9200/_cluster/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

networks:
  elasticsearch-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.21.0.0/16"""
    
    return compose_content

def generate_init_script(node, config):
    """Generate initialization script for a single node"""
    cluster_name = config['cluster_name']
    es_version = config['es_version']
    
    script_content = f"""#!/bin/bash
# Elasticsearch Node Initialization Script - {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Cluster: {cluster_name} | Version: {es_version}
# Node Type: {', '.join(node['roles']).title()}

set -e

echo "🔍 Initializing Elasticsearch Node: {node['name']}"
echo "📊 Cluster: {cluster_name}"
echo "🎭 Roles: {', '.join(node['roles'])}"
echo "💻 Hardware: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM"
echo "🌐 Network: {node['ip']}:{node['http_port']}"
echo "==============================================="

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi

echo "✅ Docker is running"

# Create required directories
echo "📁 Creating directories for {node['name']}..."
mkdir -p ./{node['name']}/{{data,logs,backups,config}}

# Set proper permissions
echo "🔧 Setting permissions..."
sudo chown -R 1000:1000 ./{node['name']}/
chmod -R 755 ./{node['name']}/

# Create basic elasticsearch.yml config
echo "📝 Creating elasticsearch.yml..."
cat > ./{node['name']}/config/elasticsearch.yml << EOF
# Elasticsearch configuration for {node['name']}
cluster.name: {cluster_name}
node.name: {node['name']}
network.host: 0.0.0.0
http.port: 9200
transport.tcp.port: 9300

# Node roles
node.master: {'true' if 'master' in node['roles'] else 'false'}
node.data: {'true' if 'data' in node['roles'] else 'false'}
node.ingest: {'true' if 'ingest' in node['roles'] else 'false'}

# Discovery (add other nodes here)
discovery.zen.ping.unicast.hosts: [{', '.join([f'"{n["hostname"]}"' for n in config['nodes']])}]

# Memory lock
bootstrap.memory_lock: true

# Performance settings
indices.memory.index_buffer_size: 25%
thread_pool.search.size: {calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])['thread_pools']['search']}
thread_pool.bulk.size: {calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])['thread_pools']['bulk']}
EOF

# Create JVM options
echo "⚙️ Creating jvm.options..."
optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
cat > ./{node['name']}/config/jvm.options << EOF
# JVM configuration for {node['name']}
-Xms{optimal['heap_size']}
-Xmx{optimal['heap_size']}

# GC Configuration
{'-XX:+UseG1GC' if optimal['jvm_settings']['gc_collector'] == 'G1GC' else '-XX:+UseConcMarkSweepGC'}

# Additional JVM options
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=data
-XX:ErrorFile=logs/hs_err_pid%p.log
-Xlog:gc*,gc+age=trace,safepoint:gc.log:utctime,pid,tags
EOF

echo "🚀 Starting {node['name']} container..."
docker-compose -f docker-compose-{node['name']}.yml up -d

echo "⏳ Waiting for {node['name']} to be ready..."
for i in {{1..30}}; do
    if curl -f http://{node['ip']}:{node['http_port']}/_cluster/health > /dev/null 2>&1; then
        echo "✅ {node['name']} is ready!"
        break
    fi
    echo "⏳ Waiting... ($i/30)"
    sleep 10
done

echo "🔍 Node {node['name']} status:"
curl -s http://{node['ip']}:{node['http_port']}/_cat/nodes?v

echo ""
echo "==============================================="
echo "✅ {node['name']} initialization complete!"
echo "🌐 HTTP URL: http://{node['ip']}:{node['http_port']}"
echo "📊 Health: http://{node['ip']}:{node['http_port']}/_cluster/health"
echo "📝 Logs: docker logs {node['name']}"
echo "==============================================="
"""
    
    return script_content

def generate_docker_compose(config):
    """Generate Docker Compose file (kept for backward compatibility)"""
    nodes = config['nodes']
    cluster_name = config['cluster_name']
    es_version = config['es_version']
    
    # Generate discovery hosts
    discovery_hosts = ','.join([node['hostname'] for node in nodes])
    
    # Calculate minimum master nodes
    master_eligible = [n for n in nodes if 'master' in n['roles']]
    min_master_nodes = calculate_minimum_master_nodes(len(nodes), len(master_eligible))
    
    compose_content = f"""version: '3.8'

services:"""
    
    for i, node in enumerate(nodes):
        optimal_settings = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
        
        # X-Pack settings
        xpack_env = []
        xpack_settings = config['xpack_settings']
        for setting, enabled in xpack_settings.items():
            if setting == 'ilm':
                xpack_env.append(f"      - xpack.ilm.enabled={'true' if enabled else 'false'}")
            else:
                xpack_env.append(f"      - xpack.{setting}.enabled={'true' if enabled else 'false'}")
        
        # Node roles
        master_eligible = 'true' if 'master' in node['roles'] else 'false'
        data_node = 'true' if 'data' in node['roles'] else 'false'
        ingest_node = 'true' if 'ingest' in node['roles'] else 'false'
        
        # JVM options based on optimal settings
        if optimal_settings['jvm_settings']['gc_collector'] == 'G1GC':
            jvm_opts = f"-Xms{optimal_settings['heap_size']} -Xmx{optimal_settings['heap_size']} " + \
                      " ".join(optimal_settings['jvm_settings']['additional_opts'])
        else:
            jvm_opts = f"-Xms{optimal_settings['heap_size']} -Xmx{optimal_settings['heap_size']} " + \
                      f"-XX:+UseConcMarkSweepGC -XX:CMSInitiatingOccupancyFraction={optimal_settings['jvm_settings']['cms_initiating_occupancy_fraction']} " + \
                      " ".join(optimal_settings['jvm_settings']['additional_opts'])
        
        compose_content += f"""
  {node['name']}:
    image: docker.elastic.co/elasticsearch/elasticsearch:{es_version}
    container_name: {node['name']}
    hostname: {node['hostname']}
    environment:
      # ==================== CLUSTER CONFIGURATION ====================
      - cluster.name={cluster_name}
      - node.name={node['name']}
      
      # ==================== NODE ROLES ====================
      - node.master={master_eligible}
      - node.data={data_node}
      - node.ingest={ingest_node}
      
      # ==================== MEMORY OPTIMIZATION ====================
      - "ES_JAVA_OPTS={jvm_opts}"
      - bootstrap.memory_lock=true
      
      # ==================== DISCOVERY CONFIGURATION ====================
      - discovery.zen.minimum_master_nodes={min_master_nodes}
      - discovery.zen.ping.unicast.hosts={discovery_hosts}
      - discovery.zen.fd.ping_timeout={optimal_settings['network_settings']['fd_ping_timeout']}
      - discovery.zen.fd.ping_retries={optimal_settings['network_settings']['fd_ping_retries']}
      - discovery.zen.publish_timeout={optimal_settings['network_settings']['publish_timeout']}
      
      # ==================== PERFORMANCE OPTIMIZATION ====================
      # Memory Management
      - indices.memory.index_buffer_size={optimal_settings['memory_settings']['index_buffer_size']}
      - indices.memory.min_index_buffer_size=128mb
      - indices.breaker.total.limit={optimal_settings['memory_settings']['total_breaker_limit']}
      - indices.breaker.request.limit={optimal_settings['memory_settings']['request_breaker_limit']}
      - indices.breaker.fielddata.limit={optimal_settings['memory_settings']['fielddata_breaker_limit']}
      
      # Cache Configuration ({node['ram_gb']}GB RAM optimized)
      - indices.queries.cache.size={optimal_settings['cache_settings']['queries_cache_size']}
      - indices.fielddata.cache.size={optimal_settings['cache_settings']['fielddata_cache_size']}
      - indices.requests.cache.size={optimal_settings['cache_settings']['requests_cache_size']}
      
      # Thread Pool Configuration ({node['cpu_cores']}-core optimized, {', '.join(node['roles'])} node)
      - thread_pool.search.size={optimal_settings['thread_pools']['search']}
      - thread_pool.search.queue_size=2000
      - thread_pool.index.size={optimal_settings['thread_pools']['index']}
      - thread_pool.index.queue_size=1000
      - thread_pool.bulk.size={optimal_settings['thread_pools']['bulk']}
      - thread_pool.bulk.queue_size=2000
      - thread_pool.write.size={optimal_settings['thread_pools']['write']}
      - thread_pool.write.queue_size=1000
      - thread_pool.get.size={optimal_settings['thread_pools']['get']}
      - thread_pool.get.queue_size=1000
      
      # ==================== RECOVERY & REBALANCING ====================
      - indices.recovery.max_bytes_per_sec={optimal_settings['recovery_settings']['max_bytes_per_sec']}
      - cluster.routing.allocation.node_concurrent_recoveries={optimal_settings['recovery_settings']['node_concurrent_recoveries']}
      - cluster.routing.allocation.node_concurrent_incoming_recoveries={optimal_settings['recovery_settings']['node_concurrent_incoming_recoveries']}
      - cluster.routing.allocation.node_concurrent_outgoing_recoveries={optimal_settings['recovery_settings']['node_concurrent_outgoing_recoveries']}
      - cluster.routing.allocation.cluster_concurrent_rebalance={optimal_settings['recovery_settings']['cluster_concurrent_rebalance']}
      
      # ==================== CLUSTER LIMITS & SAFETY ====================
      - cluster.max_shards_per_node={optimal_settings['cluster_settings']['max_shards_per_node']}
      - cluster.routing.allocation.total_shards_per_node={optimal_settings['cluster_settings']['total_shards_per_node']}
      - action.destructive_requires_name={'true' if optimal_settings['cluster_settings']['action_destructive_requires_name'] else 'false'}
      
      # ==================== PERFORMANCE MONITORING ====================
      - logger.index.search.slowlog.threshold.query.warn={optimal_settings['monitoring_settings']['slow_query_threshold_warn']}
      - logger.index.search.slowlog.threshold.query.info={optimal_settings['monitoring_settings']['slow_query_threshold_info']}
      - logger.index.search.slowlog.threshold.fetch.warn={optimal_settings['monitoring_settings']['slow_fetch_threshold_warn']}
      - logger.index.indexing.slowlog.threshold.index.warn={optimal_settings['monitoring_settings']['slow_index_threshold_warn']}
      
      # ==================== X-PACK FEATURES ====================
{chr(10).join(xpack_env)}
      
      # ==================== NETWORK CONFIGURATION ====================
      - network.host=0.0.0.0
      - network.publish_host={node['ip']}
      - transport.tcp.port=9300
      - http.port=9200
      - http.cors.enabled=true
      - http.cors.allow-origin="*"
      - transport.tcp.keep_alive={'true' if optimal_settings['network_settings']['tcp_keep_alive'] else 'false'}
      - transport.tcp.reuse_address={'true' if optimal_settings['network_settings']['tcp_reuse_address'] else 'false'}
      - transport.tcp.connect_timeout={optimal_settings['network_settings']['tcp_connect_timeout']}
      
    ports:
      - "{node['http_port']}:9200"
      - "{node['transport_port']}:9300"
      
    volumes:
      - ./{node['name']}/data:/usr/share/elasticsearch/data
      - ./{node['name']}/logs:/usr/share/elasticsearch/logs
      - ./{node['name']}/backups:/usr/share/elasticsearch/backups
      - ./{node['name']}/config:/usr/share/elasticsearch/config
      
    networks:
      - elasticsearch-net
      
    ulimits:
      memlock:
        soft: -1
        hard: -1
      nofile:
        soft: 65536
        hard: 65536
      
    # ==================== RESOURCE LIMITS ====================
    mem_limit: {node['ram_gb']}g
    cpus: '{node['cpu_cores']}.0'
    
    restart: unless-stopped
    
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:9200/_cluster/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s"""
    
    compose_content += """

networks:
  elasticsearch-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.21.0.0/16"""
    
    return compose_content

def generate_env_files(config):
    """Generate .env files for each node"""
    env_files = {}
    nodes = config['nodes']
    cluster_name = config['cluster_name']
    es_version = config['es_version']
    
    # Generate discovery hosts
    discovery_hosts = ','.join([node['hostname'] for node in nodes])
    
    # Calculate minimum master nodes
    master_eligible = [n for n in nodes if 'master' in n['roles']]
    min_master_nodes = calculate_minimum_master_nodes(len(nodes), len(master_eligible))
    
    for node in nodes:
        optimal_settings = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
        
        env_content = f"""# Elasticsearch Node Configuration - {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

# ==================== CLUSTER CONFIGURATION ====================
CLUSTER_NAME={cluster_name}
NODE_NAME={node['name']}
ES_VERSION={es_version}

# ==================== SERVER DETAILS ====================
NODE_IP={node['ip']}
NODE_HOSTNAME={node['hostname']}
NODE_ROLES={','.join(node['roles'])}
NODE_TYPE={'master-only' if node['roles'] == ['master'] else 'data-node' if 'data' in node['roles'] else 'mixed-role'}

# ==================== MEMORY SETTINGS ====================
HEAP_SIZE={optimal_settings['heap_size']}
RAM_GB={node['ram_gb']}

# ==================== DISCOVERY CONFIGURATION ====================
DISCOVERY_HOSTS={discovery_hosts}
MINIMUM_MASTER_NODES={min_master_nodes}

# ==================== NETWORK CONFIGURATION ====================
HTTP_PORT={node['http_port']}
TRANSPORT_PORT={node['transport_port']}

# ==================== PERFORMANCE SETTINGS ====================
CPU_CORES={node['cpu_cores']}
THREAD_POOL_SEARCH_SIZE={optimal_settings['thread_pools']['search']}
THREAD_POOL_INDEX_SIZE={optimal_settings['thread_pools']['index']}
THREAD_POOL_BULK_SIZE={optimal_settings['thread_pools']['bulk']}
THREAD_POOL_WRITE_SIZE={optimal_settings['thread_pools']['write']}
THREAD_POOL_GET_SIZE={optimal_settings['thread_pools']['get']}

# ==================== MEMORY OPTIMIZATION ====================
INDEX_BUFFER_SIZE={optimal_settings['memory_settings']['index_buffer_size']}
TOTAL_BREAKER_LIMIT={optimal_settings['memory_settings']['total_breaker_limit']}
REQUEST_BREAKER_LIMIT={optimal_settings['memory_settings']['request_breaker_limit']}
FIELDDATA_BREAKER_LIMIT={optimal_settings['memory_settings']['fielddata_breaker_limit']}

# ==================== CACHE CONFIGURATION ====================
QUERIES_CACHE_SIZE={optimal_settings['cache_settings']['queries_cache_size']}
FIELDDATA_CACHE_SIZE={optimal_settings['cache_settings']['fielddata_cache_size']}
REQUESTS_CACHE_SIZE={optimal_settings['cache_settings']['requests_cache_size']}

# ==================== RECOVERY SETTINGS ====================
RECOVERY_MAX_BYTES_PER_SEC={optimal_settings['recovery_settings']['max_bytes_per_sec']}
NODE_CONCURRENT_RECOVERIES={optimal_settings['recovery_settings']['node_concurrent_recoveries']}
CLUSTER_CONCURRENT_REBALANCE={optimal_settings['recovery_settings']['cluster_concurrent_rebalance']}

# ==================== JVM SETTINGS ====================
GC_COLLECTOR={optimal_settings['jvm_settings']['gc_collector']}
CMS_INITIATING_OCCUPANCY_FRACTION={optimal_settings['jvm_settings'].get('cms_initiating_occupancy_fraction', 75)}

# ==================== NETWORK SETTINGS ====================
TCP_KEEP_ALIVE={str(optimal_settings['network_settings']['tcp_keep_alive']).lower()}
TCP_REUSE_ADDRESS={str(optimal_settings['network_settings']['tcp_reuse_address']).lower()}
TCP_CONNECT_TIMEOUT={optimal_settings['network_settings']['tcp_connect_timeout']}
FD_PING_TIMEOUT={optimal_settings['network_settings']['fd_ping_timeout']}
FD_PING_RETRIES={optimal_settings['network_settings']['fd_ping_retries']}

# ==================== CLUSTER SETTINGS ====================
MAX_SHARDS_PER_NODE={optimal_settings['cluster_settings']['max_shards_per_node']}
TOTAL_SHARDS_PER_NODE={optimal_settings['cluster_settings']['total_shards_per_node']}

# ==================== MONITORING SETTINGS ====================
SLOW_QUERY_THRESHOLD_WARN={optimal_settings['monitoring_settings']['slow_query_threshold_warn']}
SLOW_QUERY_THRESHOLD_INFO={optimal_settings['monitoring_settings']['slow_query_threshold_info']}
SLOW_FETCH_THRESHOLD_WARN={optimal_settings['monitoring_settings']['slow_fetch_threshold_warn']}

# ==================== CAPACITY ESTIMATES ====================
ESTIMATED_DATA_CAPACITY_GB={optimal_settings['capacity_estimates']['data_capacity_gb']}
ESTIMATED_CONCURRENT_SEARCHES={optimal_settings['capacity_estimates']['concurrent_searches']}
ESTIMATED_INDEXING_RATE_DOCS_PER_SEC={optimal_settings['capacity_estimates']['indexing_rate_docs_per_sec']}

# ==================== X-PACK SETTINGS ====================
"""
        
        # Add X-Pack settings
        xpack_settings = config['xpack_settings']
        for setting, enabled in xpack_settings.items():
            env_content += f"XPACK_{setting.upper()}_ENABLED={'true' if enabled else 'false'}\n"
        
        env_files[f"{node['name']}.env"] = env_content
    
    return env_files

def generate_cluster_files(config):
    """Generate all files for the cluster (compose, env, init scripts)"""
    cluster_files = {}
    nodes = config['nodes']
    
    # Generate README file
    master_eligible = [n for n in nodes if 'master' in n['roles']]
    min_master_nodes = calculate_minimum_master_nodes(len(nodes), len(master_eligible))
    
    readme_content = f"""# Elasticsearch Cluster: {config['cluster_name']}
Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Cluster Overview
- **Version**: {config['es_version']}
- **Nodes**: {len(nodes)}
- **Master Eligible**: {len(master_eligible)}
- **Minimum Master Nodes**: {min_master_nodes}
- **Domain**: {config['primary_domain']}

## Node Configuration
"""
    
    for i, node in enumerate(nodes):
        optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
        readme_content += f"""
### {node['name']} ({node['ip']})
- **Roles**: {', '.join(node['roles']).title()}
- **Hardware**: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM
- **Heap Size**: {optimal['heap_size']}
- **Ports**: HTTP {node['http_port']}, Transport {node['transport_port']}
- **Est. Capacity**: ~{optimal['capacity_estimates']['data_capacity_gb']:,}GB
"""
    
    readme_content += f"""
## Quick Start Instructions

1. **Extract all files** to your deployment directory
2. **Make init scripts executable**: `chmod +x init-*.sh`
3. **Run nodes individually**:
"""
    
    for node in nodes:
        readme_content += f"   - `./init-{node['name']}.sh`\n"
    
    readme_content += f"""
4. **Or start all nodes together**:
   ```bash
   # Start all nodes
   for script in init-*.sh; do
       ./$script &
   done
   ```

## Files Included
"""
    
    # Generate files for each node
    for node in nodes:
        # Individual compose file
        compose_file = generate_individual_docker_compose(node, config)
        cluster_files[f"docker-compose-{node['name']}.yml"] = compose_file
        
        # Individual init script
        init_script = generate_init_script(node, config)
        cluster_files[f"init-{node['name']}.sh"] = init_script
        
        readme_content += f"""
### {node['name']} Files:
- `docker-compose-{node['name']}.yml` - Complete Docker Compose configuration
- `init-{node['name']}.sh` - Initialization script
"""
    
    readme_content += f"""
## Management Commands

### Check cluster health:
```bash
curl http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cluster/health?pretty
```

### View nodes:
```bash
curl http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cat/nodes?v
```

### Stop all nodes:
```bash
for node in {' '.join([node['name'] for node in nodes])}; do
    docker stop $node
done
```

### Start all nodes:
```bash
for node in {' '.join([node['name'] for node in nodes])}; do
    docker start $node
done
```

## X-Pack Features
"""
    
    for feature, enabled in config['xpack_settings'].items():
        status = "✅ Enabled" if enabled else "❌ Disabled"
        readme_content += f"- **{feature.title()}**: {status}\n"
    
    readme_content += """
## Support
- Check Docker logs: `docker logs <node-name>`
- Monitor health endpoints: `http://<node-ip>:9200/_cluster/health`
- View node stats: `http://<node-ip>:9200/_nodes/stats`
"""
    
    cluster_files["README.md"] = readme_content
    
    return cluster_files

# Create main layout - sidebar is handled by st.sidebar, main content uses full width
main_col = st.container()

# Right sidebar for configuration
with st.sidebar:
    # Ensure nodes are synchronized before displaying sidebar
    ensure_nodes_sync()
    
    st.header("📁 Configuration Management")
    st.markdown("---")
    
    st.subheader("💾 Save Configuration")
    st.caption("Download complete cluster and node configuration")
    
    # Save configuration - Direct download with comprehensive data
    config_json = save_configuration()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    
    st.download_button(
        label="💾 Download Complete Config",
        data=config_json,
        file_name=f"elasticsearch_cluster_config_{timestamp}.json",
        mime="application/json",
        use_container_width=True,
        help="Saves all cluster settings, node configurations, and X-Pack settings"
    )
    
    st.markdown("---")
    
    st.subheader("📤 Load Configuration")
    st.caption("Upload previously saved configuration file")
    
    # Load configuration
    uploaded_file = st.file_uploader(
        "📂 Choose Configuration File",
        type=['json'],
        help="Upload elasticsearch_cluster_config_*.json file",
        key="sidebar_upload"
    )
    
    if uploaded_file is not None:
        # Show file info
        try:
            file_content = json.loads(uploaded_file.getvalue().decode('utf-8'))
            if 'cluster_config' in file_content:
                cluster_name = file_content['cluster_config'].get('cluster_name', 'Unknown')
                node_count = file_content.get('node_count', 0)
                generated_at = file_content.get('generated_at', 'Unknown')
                
                st.info(f"""
                📄 **File Preview:**
                - Cluster: {cluster_name}
                - Nodes: {node_count}
                - Created: {generated_at[:16] if generated_at != 'Unknown' else 'Unknown'}
                """)
                

        except:
            st.warning("⚠️ Invalid configuration file format")
        
        if st.button("📂 Load Configuration", use_container_width=True, key="sidebar_load"):
            if load_configuration(uploaded_file):
                st.success("✅ Configuration loaded successfully!")
                st.rerun()
            else:
                st.error("❌ Failed to load configuration")
    
    st.markdown("---")
    
    # Quick cluster summary
    if len(st.session_state.cluster_config['nodes']) > 0:
        st.subheader("📊 Cluster Summary")
        
        # Ensure data is fresh for display
        master_eligible = [n for n in st.session_state.cluster_config['nodes'] if 'master' in n['roles']]
        data_nodes = [n for n in st.session_state.cluster_config['nodes'] if 'data' in n['roles']]
        master_only = [n for n in st.session_state.cluster_config['nodes'] if n['roles'] == ['master']]
        
        st.metric("Total Nodes", len(st.session_state.cluster_config['nodes']))
        st.metric("Master Eligible", len(master_eligible))
        st.metric("Data Nodes", len(data_nodes))
        st.metric("Master Only", len(master_only))
        
        total_cpu = sum(node['cpu_cores'] for node in st.session_state.cluster_config['nodes'])
        total_ram = sum(node['ram_gb'] for node in st.session_state.cluster_config['nodes'])
        st.metric("Total CPU Cores", total_cpu)
        st.metric("Total RAM", f"{total_ram}GB")
        
        # Capacity estimates
        total_capacity = 0
        for node in st.session_state.cluster_config['nodes']:
            if 'data' in node['roles']:
                optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
                total_capacity += optimal['capacity_estimates']['data_capacity_gb']
        
        if total_capacity > 0:
            st.metric("Est. Data Capacity", f"~{total_capacity:,}GB")

with main_col:
    st.title("🔍 Elasticsearch Cluster Configuration Generator")
    st.markdown("Generate production-ready Docker Compose and .env files for your Elasticsearch cluster")

    with st.expander("🚀 **New Features & Optimizations**", expanded=False):
        st.markdown("""
        **✨ Latest Updates:**
        - 🎭 **3 Node Types**: Master-only, Master+Data+Ingest, Data+Ingest only
        - 🏷️ **Short Names**: Auto-generates els01, els02, els03... format
        - 🧠 **Smart Split-brain**: Automatic minimum master calculation (3 masters → min 2)
        - ⚡ **Advanced Optimization**: 50+ settings auto-calculated based on hardware
        - 📊 **Capacity Estimates**: Data capacity, search throughput, indexing rate predictions
        - 🎯 **Role-specific Tuning**: Master-only nodes get lighter resource allocation
        - 🔧 **Production Settings**: JVM, GC, recovery, monitoring, all optimized
        - 🎯 **Left Sidebar Details**: Click "Show Details" to see optimization settings
        
        **🎯 Split-brain Formula**: `minimum_master_nodes = ⌊master_eligible_count / 2⌋ + 1`
        - 1 master → min 1 (single node)
        - 3 masters → min 2 (survives 1 failure) 
        - 5 masters → min 3 (survives 2 failures)
        """)

    tab1, tab2, tab3 = st.tabs(["🔧 Cluster Setup", "🖥️ Node Configuration", "📄 Generate Files"])

    with tab1:
        st.header("🔧 Cluster Configuration")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.session_state.cluster_config['es_version'] = st.selectbox(
                "🔢 Elasticsearch Version",
                ES_VERSIONS,
                index=ES_VERSIONS.index(st.session_state.cluster_config['es_version']),
                help="Select the Elasticsearch version to deploy"
            )
            
            # Store previous domain for comparison
            previous_domain = st.session_state.cluster_config['primary_domain']
            
            st.session_state.cluster_config['primary_domain'] = st.text_input(
                "🌐 Primary Domain",
                value=st.session_state.cluster_config['primary_domain'],
                help="Primary domain for your cluster (e.g., plugilo.com)"
            )
            
            # Update node hostnames if domain changed
            current_domain = st.session_state.cluster_config['primary_domain']
            if current_domain != previous_domain and len(st.session_state.cluster_config['nodes']) > 0:
                for i, node in enumerate(st.session_state.cluster_config['nodes']):
                    # Extract node number from existing name (e.g., "els01" -> 1)
                    node_name = node['name']
                    if node_name.startswith('els') and node_name[3:].isdigit():
                        node_number = int(node_name[3:])
                        # Update hostname with new domain
                        st.session_state.cluster_config['nodes'][i]['hostname'] = generate_hostname(node_number, current_domain)
                st.rerun()
        
        with col2:
            st.session_state.cluster_config['cluster_name'] = st.text_input(
                "📛 Cluster Name",
                value=st.session_state.cluster_config['cluster_name'],
                key="cluster_name_input",
                help="Name for your Elasticsearch cluster"
            )
            
            # Node count with +/- buttons - using inline layout
            st.markdown("🖥️ **Number of Nodes**")
            
            # Create inline layout using markdown and buttons
            button_col1, display_col, button_col2 = st.columns([1, 2, 1])
            
            with button_col1:
                decrease = st.button("➖", key="remove_node", help="Remove a node")
            
            with display_col:
                st.markdown(f"<div style='text-align: center; font-size: 1.2em; font-weight: bold; padding: 8px; margin-top: 8px;'>{st.session_state.node_count} nodes</div>", unsafe_allow_html=True)
            
            with button_col2:
                increase = st.button("➕", key="add_node", help="Add a node")
            
            # Handle button clicks and ensure nodes are created/updated
            if decrease and st.session_state.node_count > 1:
                st.session_state.node_count -= 1
                # Remove excess node when decreasing
                if len(st.session_state.cluster_config['nodes']) > st.session_state.node_count:
                    st.session_state.cluster_config['nodes'] = st.session_state.cluster_config['nodes'][:st.session_state.node_count]
                st.rerun()
            elif increase and st.session_state.node_count < 20:
                st.session_state.node_count += 1
                # Add new node when increasing
                current_nodes = len(st.session_state.cluster_config['nodes'])
                if current_nodes < st.session_state.node_count:
                    new_node = {
                        'name': generate_node_name(current_nodes + 1, st.session_state.cluster_config['primary_domain']),
                        'hostname': generate_hostname(current_nodes + 1, st.session_state.cluster_config['primary_domain']),
                        'roles': ['master', 'data', 'ingest'],
                        'cpu_cores': 8,
                        'ram_gb': 32,
                        'ip': f"10.0.1.{10 + current_nodes}",
                        'http_port': 9200,
                        'transport_port': 9300
                    }
                    st.session_state.cluster_config['nodes'].append(new_node)
                st.rerun()
            
            st.caption("Use +/- buttons to adjust node count (max 20)")
        
        # X-Pack Configuration
        st.subheader("🛡️ X-Pack Features Configuration")
        
        xpack_col1, xpack_col2 = st.columns(2)
        
        with xpack_col1:
            st.session_state.cluster_config['xpack_settings']['security'] = st.checkbox(
                "🔐 Security", 
                value=st.session_state.cluster_config['xpack_settings']['security'],
                help="Enable X-Pack security features"
            )
            
            st.session_state.cluster_config['xpack_settings']['monitoring'] = st.checkbox(
                "📊 Monitoring", 
                value=st.session_state.cluster_config['xpack_settings']['monitoring'],
                help="Enable X-Pack monitoring features"
            )
            
            st.session_state.cluster_config['xpack_settings']['ml'] = st.checkbox(
                "🤖 Machine Learning", 
                value=st.session_state.cluster_config['xpack_settings']['ml'],
                help="Enable X-Pack machine learning features"
            )
        
        with xpack_col2:
            st.session_state.cluster_config['xpack_settings']['watcher'] = st.checkbox(
                "👁️ Watcher", 
                value=st.session_state.cluster_config['xpack_settings']['watcher'],
                help="Enable X-Pack watcher (alerting) features"
            )
            
            st.session_state.cluster_config['xpack_settings']['graph'] = st.checkbox(
                "🕸️ Graph", 
                value=st.session_state.cluster_config['xpack_settings']['graph'],
                help="Enable X-Pack graph features"
            )
            
            st.session_state.cluster_config['xpack_settings']['ilm'] = st.checkbox(
                "🔄 Index Lifecycle Management", 
                value=st.session_state.cluster_config['xpack_settings']['ilm'],
                help="Enable X-Pack Index Lifecycle Management (recommended)"
            )

    with tab2:
        st.header("🖥️ Node Configuration")
        
        # Ensure nodes list matches node count
        current_nodes = len(st.session_state.cluster_config['nodes'])
        if current_nodes < st.session_state.node_count:
            # Add new nodes
            for i in range(current_nodes, st.session_state.node_count):
                new_node = {
                    'name': generate_node_name(i + 1, st.session_state.cluster_config['primary_domain']),
                    'hostname': generate_hostname(i + 1, st.session_state.cluster_config['primary_domain']),
                    'roles': ['master', 'data', 'ingest'],
                    'cpu_cores': 8,
                    'ram_gb': 32,
                    'ip': f"10.0.1.{10 + i}",
                    'http_port': 9200,
                    'transport_port': 9300
                }
                st.session_state.cluster_config['nodes'].append(new_node)
        elif current_nodes > st.session_state.node_count:
            # Remove excess nodes
            st.session_state.cluster_config['nodes'] = st.session_state.cluster_config['nodes'][:st.session_state.node_count]
        
        # Node configuration in responsive grid layout
        nodes = st.session_state.cluster_config['nodes']
        if len(nodes) > 0:
            # Display all node configurations first
            # Then show validation after user interactions
            # Calculate grid layout (max 3 columns)
            cols_per_row = min(3, len(nodes))
            rows_needed = (len(nodes) + cols_per_row - 1) // cols_per_row
            
            for row in range(rows_needed):
                # Create columns for this row
                grid_cols = st.columns(cols_per_row)
                
                # Fill columns in this row
                for col_idx in range(cols_per_row):
                    node_idx = row * cols_per_row + col_idx
                    if node_idx < len(nodes):
                        with grid_cols[col_idx]:
                            node = nodes[node_idx]
                            i = node_idx
                            
                            # Node configuration using st.expander
                            with st.expander(f"🖥️ Node {i+1}: {node['name']}", expanded=True):
                                    # Node role selection
                                    current_role_key = 'master_only' if node['roles'] == ['master'] else \
                                                      'master_data_ingest' if 'master' in node['roles'] else 'data_ingest'
                                    role_selection = st.radio(
                                        "🎭 Node Role",
                                        options=list(NODE_ROLES.keys()),
                                        format_func=lambda x: NODE_ROLES[x]['label'],
                                        index=list(NODE_ROLES.keys()).index(current_role_key),
                                        key=f"role_{i}",
                                        help="Select the roles for this node"
                                    )
                                    st.session_state.cluster_config['nodes'][i]['roles'] = NODE_ROLES[role_selection]['roles']
                                    st.caption(f"📝 {NODE_ROLES[role_selection]['description']}")
                                    
                                    # Basic configuration in compact layout
                                    st.session_state.cluster_config['nodes'][i]['name'] = st.text_input(
                                        "📛 Node Name",
                                        value=node['name'],
                                        key=f"name_{i}",
                                        help="Unique name for this node"
                                    )
                                    
                                    st.session_state.cluster_config['nodes'][i]['hostname'] = st.text_input(
                                        "🌐 Hostname",
                                        value=node['hostname'],
                                        key=f"hostname_{i}",
                                        help="Hostname for this node"
                                    )
                                    
                                    st.session_state.cluster_config['nodes'][i]['ip'] = st.text_input(
                                        "🌐 IP Address",
                                        value=node['ip'],
                                        key=f"ip_{i}",
                                        help="IP address for this node"
                                    )
                                    
                                    # Hardware configuration - vertical layout to avoid nesting columns
                                    st.session_state.cluster_config['nodes'][i]['cpu_cores'] = st.number_input(
                                        "⚙️ CPU Cores",
                                        min_value=1,
                                        max_value=64,
                                        value=node['cpu_cores'],
                                        key=f"cpu_{i}",
                                        help="CPU cores"
                                    )
                                    
                                    st.session_state.cluster_config['nodes'][i]['ram_gb'] = st.number_input(
                                        "💾 RAM (GB)",
                                        min_value=2,
                                        max_value=1024,
                                        value=node['ram_gb'],
                                        key=f"ram_{i}",
                                        help="RAM in GB"
                                    )
                                    
                                    st.session_state.cluster_config['nodes'][i]['http_port'] = st.number_input(
                                        "🔌 HTTP Port",
                                        min_value=1024,
                                        max_value=65535,
                                        value=node['http_port'],
                                        key=f"http_port_{i}",
                                        help="HTTP port"
                                    )
                                    
                                    st.session_state.cluster_config['nodes'][i]['transport_port'] = st.number_input(
                                        "🔌 Transport Port",
                                        min_value=1024,
                                        max_value=65535,
                                        value=node['transport_port'],
                                        key=f"transport_port_{i}",
                                        help="Transport port"
                                    )
                                    
                                    # Calculate optimal settings for tabs
                                    optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
                                    node_type = "Master-Only" if node['roles'] == ['master'] else "Data Node" if 'data' in node['roles'] else "Mixed"
                                    
                                    # Tabs for Summary and Details
                                    tab1, tab2 = st.tabs(["📊 Quick Summary", "🎯 Details"])
                                    
                                    with tab1:
                                        # Role-specific recommendations  
                                        if role_selection == 'master_only':
                                            st.info("💡 **Master-only**: Lightweight, cluster coordination. Use lower CPU/RAM.")
                                        elif role_selection == 'data_ingest':
                                            st.info("💡 **Data node**: Heavy lifting for storage/search. Use higher CPU/RAM.")
                                        else:
                                            st.info("💡 **Mixed-role**: Balanced approach for small-medium clusters.")
                                        
                                        # Quick metrics
                                        st.metric("🧠 Heap Size", optimal['heap_size'], f"50% of {node['ram_gb']}GB RAM")
                                        st.metric("🔍 Search Threads", optimal['thread_pools']['search'])
                                        st.metric("📊 Est. Capacity", f"~{optimal['capacity_estimates']['data_capacity_gb']:,}GB")
                                        
                                        # Capacity overview
                                        st.markdown("**Performance Estimates:**")
                                        st.text(f"• Concurrent Searches: ~{optimal['capacity_estimates']['concurrent_searches']:,}")
                                        st.text(f"• Indexing Rate: ~{optimal['capacity_estimates']['indexing_rate_docs_per_sec']:,}/sec")
                                    
                                    with tab2:
                                        st.markdown(f"**Node Type:** {node_type} | **Hardware:** {node['cpu_cores']} cores, {node['ram_gb']}GB RAM")
                                        
                                        # Memory Configuration - vertical layout to avoid nesting
                                        st.markdown("##### 💾 Memory Settings")
                                        st.metric("Index Buffer", optimal['memory_settings']['index_buffer_size'])
                                        st.metric("Total Breaker", optimal['memory_settings']['total_breaker_limit'])
                                        st.metric("Query Cache", optimal['cache_settings']['queries_cache_size'])
                                        st.metric("Request Cache", optimal['cache_settings']['requests_cache_size'])
                                        
                                        # Thread Pool Configuration
                                        st.markdown("##### ⚡ Thread Pools")
                                        thread_data = {
                                            "Pool": ["Search", "Index", "Bulk", "Write"],
                                            "Threads": [
                                                optimal['thread_pools']['search'],
                                                optimal['thread_pools']['index'],
                                                optimal['thread_pools']['bulk'],
                                                optimal['thread_pools']['write']
                                            ]
                                        }
                                        st.dataframe(thread_data, use_container_width=True, hide_index=True)
                                        
                                        # JVM & Recovery
                                        st.markdown("##### 🧠 JVM & Recovery")
                                        st.text(f"GC: {optimal['jvm_settings']['gc_collector']}")
                                        st.text(f"Recovery: {optimal['recovery_settings']['max_bytes_per_sec']}")
                                        st.text(f"Recoveries: {optimal['recovery_settings']['node_concurrent_recoveries']}")
                                        
                                        # Role-specific info
                                        if node_type == "Master-Only":
                                            st.info("💡 **Master-Only**: Optimized for cluster coordination with conservative resources")
                                        else:
                                            data_capacity = optimal['capacity_estimates']['data_capacity_gb']
                                            st.success(f"💡 **Data Node**: ~{data_capacity:,}GB primary data capacity")
                                        
                                        # Monitoring thresholds
                                        st.markdown("##### 🔍 Monitoring")
                                        st.text(f"Slow Query Warning: {optimal['monitoring_settings']['slow_query_threshold_warn']}")
                                        st.text(f"Slow Fetch Warning: {optimal['monitoring_settings']['slow_fetch_threshold_warn']}")
            
            # Live validation section - updates when node roles change
            st.markdown("---")
            st.subheader("🔍 Cluster Validation")
            
            # Calculate master node info with fresh data
            master_eligible = [n for n in st.session_state.cluster_config['nodes'] if 'master' in n['roles']]
            master_only_nodes = [n for n in st.session_state.cluster_config['nodes'] if n['roles'] == ['master']]
            master_data_ingest_nodes = [n for n in st.session_state.cluster_config['nodes'] if set(n['roles']) == {'master', 'data', 'ingest'}]
            data_only_nodes = [n for n in st.session_state.cluster_config['nodes'] if set(n['roles']) == {'data', 'ingest'}]
            total_nodes = len(st.session_state.cluster_config['nodes'])
            min_master_nodes = calculate_minimum_master_nodes(total_nodes, len(master_eligible))
            
            # Live validation logic that updates when roles change
            if len(master_eligible) == 0:
                st.error(f"""
                🚫 **CRITICAL ERROR**: No master-eligible nodes configured!
                - **Current Setup**: {len(data_only_nodes)} Data+Ingest nodes (NOT master-eligible)
                - **Required**: At least one node must be "Master Only" or "Master + Data + Ingest"
                - **Solution**: Change at least one node role to include master capability
                """)
            elif len(master_eligible) == 2:
                st.error(f"""
                🚫 **SPLIT-BRAIN RISK**: You have {len(master_eligible)} master-eligible nodes!
                - **Current Setup**: 
                  - Master Only: {len(master_only_nodes)} nodes
                  - Master + Data + Ingest: {len(master_data_ingest_nodes)} nodes
                  - Data + Ingest Only: {len(data_only_nodes)} nodes
                - **Problem**: Even number of masters (2) can cause split-brain scenarios
                - **Solution**: Use 1, 3, 5, or 7 master-eligible nodes (odd numbers)
                - **Recommendation**: Add 1 more master node or remove 1 master role
                """)
            elif total_nodes >= 3 and len(master_eligible) == 1:
                st.warning(f"""
                ⚠️ **SINGLE POINT OF FAILURE**: Only 1 master node in a {total_nodes}-node cluster!
                - **Current Setup**: 
                  - Master-eligible: {len(master_eligible)} nodes
                  - Data+Ingest Only: {len(data_only_nodes)} nodes
                - **Risk**: If master node fails, cluster becomes read-only
                - **Recommendation**: Use 3 master-eligible nodes for production clusters
                - **Current**: No fault tolerance for master node failure
                """)
            elif total_nodes >= 6 and len(master_eligible) < 3:
                st.warning(f"""
                ⚠️ **INSUFFICIENT MASTERS**: Only {len(master_eligible)} master nodes for {total_nodes} total nodes!
                - **Current Setup**: 
                  - Master-eligible: {len(master_eligible)} nodes (Master Only + Master+Data+Ingest)
                  - Data+Ingest Only: {len(data_only_nodes)} nodes
                - **Recommendation**: Use at least 3 master-eligible nodes for clusters with 6+ nodes
                - **Current**: Limited fault tolerance ({len(master_eligible)-1} master failure max)
                - **Best Practice**: 3-5 dedicated master nodes for large clusters
                """)
            else:
                # All good - show success info
                if len(master_eligible) == 1:
                    protection_msg = "✅ Single-node setup (development/testing)"
                elif len(master_eligible) == 3:
                    protection_msg = "✅ Optimal production setup (survives 1 master failure)"
                elif len(master_eligible) == 5:
                    protection_msg = "✅ High-availability setup (survives 2 master failures)"
                else:
                    protection_msg = f"✅ Cluster requires {min_master_nodes} masters to be active"
                
                st.success(f"""
                📊 **Split-brain Prevention Analysis**: 
                - **Master-eligible nodes**: {len(master_eligible)} out of {total_nodes} total
                  - Master Only: {len(master_only_nodes)} nodes
                  - Master + Data + Ingest: {len(master_data_ingest_nodes)} nodes
                  - Data + Ingest Only: {len(data_only_nodes)} nodes (NOT master-eligible)
                - **Minimum master nodes**: {min_master_nodes} (formula: ⌊{len(master_eligible)}/2⌋ + 1)
                - **Split-brain protection**: {protection_msg}
                """)
                
        else:
            st.info("📝 Add nodes by increasing the 'Number of Nodes' in the Cluster Setup tab.")

    with tab3:
        st.header("📄 Generate Configuration Files")
        
        if len(st.session_state.cluster_config['nodes']) == 0:
            st.warning("⚠️ Please configure at least one node in the Node Configuration tab.")
        else:
            # Validation
            validation_issues = []
            
            # Check for duplicate names
            names = [node['name'] for node in st.session_state.cluster_config['nodes']]
            if len(names) != len(set(names)):
                validation_issues.append("❌ Duplicate node names detected")
            
            # Check for duplicate IPs
            ips = [node['ip'] for node in st.session_state.cluster_config['nodes']]
            if len(ips) != len(set(ips)):
                validation_issues.append("❌ Duplicate IP addresses detected")
            
            # Check master nodes
            master_eligible = [n for n in st.session_state.cluster_config['nodes'] if 'master' in n['roles']]
            if len(master_eligible) == 0:
                validation_issues.append("❌ At least one master-eligible node required")
            
            if validation_issues:
                st.error("**Validation Issues:**\n" + "\n".join(validation_issues))
            else:
                st.success("✅ Configuration validated successfully!")
                
                # Generate cluster files
                st.info("💡 **New Feature**: Individual files for each node - compose, env, and init scripts!")
                
                if st.button("🚀 Generate Cluster Files", use_container_width=True, type="primary"):
                    cluster_files = generate_cluster_files(st.session_state.cluster_config)
                    
                    # Create a ZIP file with all cluster files
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                        for filename, content in cluster_files.items():
                            zip_file.writestr(filename, content)
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cluster_name = st.session_state.cluster_config['cluster_name'].replace(' ', '_').lower()
                    
                    st.download_button(
                        label="📦 Download Complete Cluster Package",
                        data=zip_buffer.getvalue(),
                        file_name=f"{cluster_name}_elasticsearch_cluster_{timestamp}.zip",
                        mime="application/zip",
                        use_container_width=True,
                        help="Contains complete Docker Compose files, init scripts, and README for each node"
                    )
                
                # Preview generated files
                if st.checkbox("👁️ Preview Individual Node Files"):
                    nodes = st.session_state.cluster_config['nodes']
                    
                    # Create tabs for each node
                    if len(nodes) > 0:
                        tab_names = [f"📄 {node['name']}" for node in nodes]
                        node_tabs = st.tabs(tab_names)
                        
                        for i, node in enumerate(nodes):
                            with node_tabs[i]:
                                st.markdown(f"### 🖥️ {node['name']} Configuration Files")
                                st.markdown(f"**Roles**: {', '.join(node['roles']).title()} | **Hardware**: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM")
                                
                                # Show compose file for this node
                                st.subheader("🐳 Docker Compose File")
                                compose_content = generate_individual_docker_compose(node, st.session_state.cluster_config)
                                st.code(compose_content, language='yaml')
                                
                                # Show note about integrated settings
                                st.info("✅ **All settings integrated**: Environment variables, X-Pack features, and configurations are included directly in the Docker Compose file above.")
                                
                                # Show init script for this node
                                st.subheader("🚀 Initialization Script")
                                init_content = generate_init_script(node, st.session_state.cluster_config)
                                with st.expander(f"View init-{node['name']}.sh", expanded=False):
                                    st.code(init_content, language='bash')
                                
                                # Show file summary
                                st.info(f"""
                                📦 **Files for {node['name']}**:
                                - `docker-compose-{node['name']}.yml` - Complete Docker Compose with all settings
                                - `init-{node['name']}.sh` - Automated setup and start script
                                - **Extra Hosts**: Automatic hostname resolution for all cluster nodes
                                """)
                    else:
                        st.warning("No nodes configured for preview")

    # Footer
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; color: #666;'>
        <p>🔍 <strong>Elasticsearch Cluster Configuration Generator</strong></p>
        <p>Generate production-ready configurations with optimal settings</p>
        <p>💡 <strong>Tip:</strong> Use the "Details" tab in each node and expand/collapse controls for comprehensive tuning information</p>
    </div>
    """, unsafe_allow_html=True)