"""
Elasticsearch Cluster Configuration Generator
===========================================

A comprehensive tool for generating production-ready Elasticsearch clusters with Docker Compose.

🆕 **VERSION-SPECIFIC COMPATIBILITY UPDATE** 🆕
===============================================
This tool now generates version-specific configurations for Elasticsearch v6.x, v7.x, and v8.x:

**v6.x Configuration:**
- Uses discovery.zen.ping.unicast.hosts for cluster discovery
- Uses discovery.zen.minimum_master_nodes for split-brain prevention
- Node roles: node.master, node.data, node.ingest (boolean flags)
- X-Pack settings with basic syntax (no ILM support)

**v7.x Configuration:**  
- Hybrid approach: zen discovery (v7.0-7.6) or seed hosts (v7.7+)
- cluster.initial_master_nodes for bootstrap
- Node roles: legacy boolean flags (compatible)
- Full X-Pack support including ILM

**v8.x Configuration:**
- Modern discovery.seed_hosts configuration
- cluster.initial_master_nodes for bootstrap  
- Node roles: node.roles array syntax ["master", "data", "ingest"]
- Enhanced X-Pack with explicit security settings (disabled by default)
- Security features explicitly configured due to v8 defaults

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
    
    # **UPDATED JVM SETTINGS** - Container-friendly with stderr logging
    if heap_gb <= 8:
        jvm_settings = {
            'gc_collector': 'ConcMarkSweep',
            'cms_initiating_occupancy_fraction': 75,
            'container_logging': True,  # NEW: Enable container-friendly logging
            'additional_opts': [
                '-XX:+UseCMSInitiatingOccupancyOnly',
                '-XX:+CMSParallelRemarkEnabled',
                '-XX:+UseCMSCompactAtFullCollection',
                # Container-friendly GC logging to stderr
                '-Xlog:disable',
                '-Xlog:all=warning:stderr:utctime,level,tags',
                '-Xlog:gc=debug:stderr:utctime',
                # Memory and performance options
                '-XX:+HeapDumpOnOutOfMemoryError',
                '-XX:+ExitOnOutOfMemoryError',
                '-Djava.awt.headless=true',
                '-Dfile.encoding=UTF-8'
            ]
        }
    else:
        jvm_settings = {
            'gc_collector': 'G1GC',
            'g1_heap_region_size': '16m',
            'container_logging': True,  # NEW: Enable container-friendly logging
            'additional_opts': [
                '-XX:+UseG1GC',
                '-XX:G1HeapRegionSize=16m',
                '-XX:MaxGCPauseMillis=200',
                '-XX:+UseStringDeduplication',
                # Container-friendly GC logging to stderr
                '-Xlog:disable',
                '-Xlog:all=warning:stderr:utctime,level,tags', 
                '-Xlog:gc=debug:stderr:utctime',
                # Memory and performance options
                '-XX:+HeapDumpOnOutOfMemoryError',
                '-XX:+ExitOnOutOfMemoryError',
                '-Djava.awt.headless=true',
                '-Dfile.encoding=UTF-8'
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

def get_version_specific_settings(es_version, node, nodes, cluster_name, config):
    """Generate version-specific Elasticsearch settings with full v6/v7/v8 compatibility"""
    version_major = int(es_version.split('.')[0])
    version_minor = int(es_version.split('.')[1]) if len(es_version.split('.')) > 1 else 0
    
    # Calculate minimum master nodes (only for v6 and early v7)
    master_eligible = [n for n in nodes if 'master' in n['roles']]
    min_master_nodes = calculate_minimum_master_nodes(len(nodes), len(master_eligible))
    
    discovery_settings = []
    role_settings = []
    xpack_settings = []
    
    # Version-specific discovery and cluster settings
    if version_major == 6:
        # ===== ELASTICSEARCH 6.x CONFIGURATION =====
        # Uses Zen Discovery with unicast hosts and minimum master nodes
        discovery_hosts = ','.join([n['hostname'] for n in nodes])
        discovery_settings = [
            f"      - discovery.zen.ping.unicast.hosts={discovery_hosts}",
            f"      - discovery.zen.minimum_master_nodes={min_master_nodes}",
            "      - discovery.zen.fd.ping_timeout=30s",
            "      - discovery.zen.fd.ping_retries=3",
            "      - discovery.zen.fd.ping_interval=5s",
            "      - discovery.zen.publish_timeout=60s",
            "      - discovery.zen.commit_timeout=30s"
        ]
        
        # Node roles for v6 (boolean flags)
        master_eligible_flag = 'true' if 'master' in node['roles'] else 'false'
        data_node = 'true' if 'data' in node['roles'] else 'false'
        ingest_node = 'true' if 'ingest' in node['roles'] else 'false'
        
        role_settings = [
            f"      - node.master={master_eligible_flag}",
            f"      - node.data={data_node}",
            f"      - node.ingest={ingest_node}"
        ]
        
        # X-Pack for v6 (basic syntax, no ILM, security usually disabled)
        xpack_config = config['xpack_settings']
        # Security disabled by default in v6
        xpack_settings.append("      - xpack.security.enabled=false")
        xpack_settings.append("      - xpack.ml.enabled=false")  # Usually disabled in production
        
        for setting, enabled in xpack_config.items():
            if setting == 'ilm':
                continue  # ILM not available in v6
            elif setting == 'security':
                if enabled:
                    xpack_settings[0] = "      - xpack.security.enabled=true"
            elif setting == 'ml':
                xpack_settings[1] = f"      - xpack.ml.enabled={'true' if enabled else 'false'}"
            else:
                xpack_settings.append(f"      - xpack.{setting}.enabled={'true' if enabled else 'false'}")
            
    elif version_major == 7:
        # ===== ELASTICSEARCH 7.x CONFIGURATION =====
        # Discovery changes significantly in 7.x series
        if version_minor >= 7:
            # v7.7+ uses seed_hosts and initial_master_nodes
            discovery_hosts = ','.join([f'"{n["hostname"]}:9300"' for n in nodes])
            master_nodes_list = ','.join([f'"{n["name"]}"' for n in master_eligible])
            discovery_settings = [
                f"      - discovery.seed_hosts=[{discovery_hosts}]",
                f"      - cluster.initial_master_nodes=[{master_nodes_list}]",
                "      - discovery.probe.connect_timeout=10s",
                "      - discovery.probe.handshake_timeout=10s"
            ]
        else:
            # v7.0-7.6 hybrid: zen discovery + initial master nodes
            discovery_hosts = ','.join([n['hostname'] for n in nodes])
            master_nodes_list = ','.join([f'"{n["name"]}"' for n in master_eligible])
            discovery_settings = [
                f"      - discovery.zen.ping.unicast.hosts={discovery_hosts}",
                f"      - discovery.zen.minimum_master_nodes={min_master_nodes}",
                f"      - cluster.initial_master_nodes=[{master_nodes_list}]",
                "      - discovery.zen.fd.ping_timeout=30s"
            ]
        
        # Node roles for v7 (boolean flags still work, array syntax available)
        master_eligible_flag = 'true' if 'master' in node['roles'] else 'false'
        data_node = 'true' if 'data' in node['roles'] else 'false'
        ingest_node = 'true' if 'ingest' in node['roles'] else 'false'
        
        role_settings = [
            f"      - node.master={master_eligible_flag}",
            f"      - node.data={data_node}",
            f"      - node.ingest={ingest_node}"
        ]
        
        # X-Pack for v7 (ILM available, security still disabled by default)
        xpack_config = config['xpack_settings']
        xpack_settings.append("      - xpack.security.enabled=false")
        
        for setting, enabled in xpack_config.items():
            if setting == 'security':
                if enabled:
                    xpack_settings[0] = "      - xpack.security.enabled=true"
                    # Add basic security settings
                    xpack_settings.extend([
                        "      - xpack.security.authc.api_key.enabled=true",
                        "      - xpack.security.transport.ssl.enabled=false"  # For development
                    ])
            elif setting == 'ilm':
                xpack_settings.append(f"      - xpack.ilm.enabled={'true' if enabled else 'false'}")
            else:
                xpack_settings.append(f"      - xpack.{setting}.enabled={'true' if enabled else 'false'}")
                
    elif version_major == 8:
        # ===== ELASTICSEARCH 8.x CONFIGURATION =====
        # Modern discovery with seed_hosts, security enabled by default
        discovery_hosts = ','.join([f'"{n["hostname"]}:9300"' for n in nodes])
        master_nodes_list = ','.join([f'"{n["name"]}"' for n in master_eligible])
        discovery_settings = [
            f"      - discovery.seed_hosts=[{discovery_hosts}]",
            f"      - cluster.initial_master_nodes=[{master_nodes_list}]",
            "      - discovery.probe.connect_timeout=10s",
            "      - discovery.probe.handshake_timeout=10s",
            "      - cluster.fault_detection.leader_check.timeout=15s"
        ]
        
        # Node roles for v8 (array syntax preferred)
        roles_array = '[' + ','.join([f'"{role}"' for role in node['roles']]) + ']'
        role_settings = [
            f"      - node.roles={roles_array}"
        ]
        
        # X-Pack for v8 (security enabled by default, must be explicitly disabled)
        xpack_config = config['xpack_settings']
        
        if not xpack_config.get('security', False):
            # Explicitly disable security for development
            xpack_settings.extend([
                "      - xpack.security.enabled=false",
                "      - xpack.security.enrollment.enabled=false",
                "      - xpack.security.http.ssl.enabled=false",
                "      - xpack.security.transport.ssl.enabled=false"
            ])
        else:
            # Enable security with proper configuration
            xpack_settings.extend([
                "      - xpack.security.enabled=true",
                "      - xpack.security.authc.api_key.enabled=true",
                "      - xpack.security.http.ssl.enabled=false",  # Development mode
                "      - xpack.security.transport.ssl.enabled=false"  # Development mode
            ])
            
        for setting, enabled in xpack_config.items():
            if setting == 'security':
                continue  # Already handled above
            elif setting == 'ilm':
                xpack_settings.append(f"      - xpack.ilm.enabled={'true' if enabled else 'false'}")
            else:
                xpack_settings.append(f"      - xpack.{setting}.enabled={'true' if enabled else 'false'}")
    
    return {
        'discovery_settings': discovery_settings,
        'role_settings': role_settings,
        'xpack_settings': xpack_settings,
        'version_major': version_major,
        'version_minor': version_minor
    }

def generate_individual_docker_compose(node, config):
    """Generate individual Docker Compose file for a single node with version-specific settings"""
    cluster_name = config['cluster_name']
    es_version = config['es_version']
    nodes = config['nodes']
    
    # Get version-specific configuration
    version_config = get_version_specific_settings(es_version, node, nodes, cluster_name, config)
    
    optimal_settings = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
    
    # **UPDATED VERSION-SPECIFIC JVM OPTIONS** with container-friendly logging
    base_jvm_opts = f"-Xms{optimal_settings['heap_size']} -Xmx{optimal_settings['heap_size']}"
    
    # Version-specific JVM options with proper container logging
    if version_config['version_major'] == 6:
        # v6 - Use legacy GC options, avoid modern logging syntax
        if optimal_settings['jvm_settings']['gc_collector'] == 'G1GC':
            version_specific_opts = [
                '-XX:+UseG1GC',
                '-XX:MaxGCPauseMillis=250',
                '-XX:G1HeapRegionSize=16m',
                # Legacy logging for v6 (no -Xlog support)
                '-XX:+PrintGC',
                '-XX:+PrintGCDetails',
                '-XX:+PrintGCTimeStamps',
                '-XX:+PrintGCApplicationStoppedTime',
                '-Xloggc:/dev/stderr'  # Direct GC log to stderr for containers
            ]
        else:
            version_specific_opts = [
                '-XX:+UseConcMarkSweepGC',
                f'-XX:CMSInitiatingOccupancyFraction={optimal_settings["jvm_settings"]["cms_initiating_occupancy_fraction"]}',
                '-XX:+UseCMSInitiatingOccupancyOnly',
                '-XX:+CMSParallelRemarkEnabled',
                '-XX:+UseCMSCompactAtFullCollection',
                # Legacy logging for v6
                '-XX:+PrintGC',
                '-XX:+PrintGCDetails', 
                '-XX:+PrintGCTimeStamps',
                '-Xloggc:/dev/stderr'
            ]
        
        # Add common v6 options
        version_specific_opts.extend([
            '-XX:+HeapDumpOnOutOfMemoryError',
            '-XX:HeapDumpPath=/usr/share/elasticsearch/data',
            '-XX:ErrorFile=/usr/share/elasticsearch/logs/hs_err_pid%p.log',
            '-Djava.awt.headless=true',
            '-Dfile.encoding=UTF-8'
        ])
    
    elif version_config['version_major'] == 7:
        # v7 - Modern JVM with container logging support
        if optimal_settings['jvm_settings']['gc_collector'] == 'G1GC':
            version_specific_opts = [
                '-XX:+UseG1GC',
                '-XX:MaxGCPauseMillis=200',
                '-XX:G1HeapRegionSize=16m',
                '-XX:+UseStringDeduplication'
            ]
        else:
            # Use G1GC by default for v7 (CMS deprecated)
            version_specific_opts = [
                '-XX:+UseG1GC',
                '-XX:MaxGCPauseMillis=200',
                '-XX:G1HeapRegionSize=16m'
            ]
        
        # v7 supports modern logging
        version_specific_opts.extend([
            # Modern GC logging to stderr for containers
            '-Xlog:disable',
            '-Xlog:all=warning:stderr:utctime,level,tags',
            '-Xlog:gc=debug:stderr:utctime',
            # Memory and error handling
            '-XX:+HeapDumpOnOutOfMemoryError',
            '-XX:HeapDumpPath=/usr/share/elasticsearch/data',
            '-XX:ErrorFile=/usr/share/elasticsearch/logs/hs_err_pid%p.log',
            '-XX:+ExitOnOutOfMemoryError',
            '-Djava.awt.headless=true',
            '-Dfile.encoding=UTF-8'
        ])
    
    elif version_config['version_major'] == 8:
        # v8 - Latest JVM options with enhanced container support
        version_specific_opts = [
            '-XX:+UseG1GC',
            '-XX:MaxGCPauseMillis=200',
            '-XX:G1HeapRegionSize=16m',
            '-XX:+UseStringDeduplication',
            # Modern container-friendly logging
            '-Xlog:disable',
            '-Xlog:all=warning:stderr:utctime,level,tags',
            '-Xlog:gc=debug:stderr:utctime',
            # Enhanced memory and error handling
            '-XX:+HeapDumpOnOutOfMemoryError',
            '-XX:HeapDumpPath=/usr/share/elasticsearch/data',
            '-XX:ErrorFile=/usr/share/elasticsearch/logs/hs_err_pid%p.log',
            '-XX:+ExitOnOutOfMemoryError',
            '-XX:+CrashOnOutOfMemoryError',
            '-Djava.awt.headless=true',
            '-Dfile.encoding=UTF-8'
        ]
    
    # Build complete JVM options string
    jvm_opts = f"{base_jvm_opts} " + " ".join(version_specific_opts)
    
    # Generate extra_hosts entries for all nodes in cluster
    extra_hosts_list = []
    for cluster_node in nodes:
        if cluster_node['name'] != node['name']:  # Don't add self
            extra_hosts_list.append(f"      - \"{cluster_node['hostname']}:{cluster_node['ip']}\"")
    
    extra_hosts_content = "\n".join(extra_hosts_list)
    
    # Version-specific comments and warnings
    version_comment = ""
    version_warnings = ""
    if version_config['version_major'] == 6:
        version_comment = "# Elasticsearch 6.x Configuration - Uses Zen Discovery with unicast hosts"
        version_warnings = "# ⚠️  Remember: v6 uses discovery.zen.minimum_master_nodes for split-brain protection"
    elif version_config['version_major'] == 7:
        if version_config['version_minor'] >= 7:
            version_comment = "# Elasticsearch 7.x Configuration - Modern discovery with seed_hosts"
        else:
            version_comment = "# Elasticsearch 7.x Configuration - Hybrid zen + initial_master_nodes"
        version_warnings = "# ⚠️  Remember: Remove cluster.initial_master_nodes after first cluster startup!"
    elif version_config['version_major'] == 8:
        version_comment = "# Elasticsearch 8.x Configuration - Modern discovery with explicit security settings"
        version_warnings = "# ⚠️  Security is disabled for development - enable for production!"
    
    # Version-specific thread pool settings
    thread_pool_settings = []
    if version_config['version_major'] <= 7:
        # v6 and v7 use different thread pool names
        thread_pool_settings = [
            f"      - thread_pool.search.size={optimal_settings['thread_pools']['search']}",
            "      - thread_pool.search.queue_size=2000",
            f"      - thread_pool.index.size={optimal_settings['thread_pools']['index']}",
            "      - thread_pool.index.queue_size=1000",
            f"      - thread_pool.bulk.size={optimal_settings['thread_pools']['bulk']}",
            "      - thread_pool.bulk.queue_size=2000",
            f"      - thread_pool.write.size={optimal_settings['thread_pools']['write']}",
            "      - thread_pool.write.queue_size=1000",
            f"      - thread_pool.get.size={optimal_settings['thread_pools']['get']}",
            "      - thread_pool.get.queue_size=1000"
        ]
    else:
        # v8+ has simplified thread pool configuration
        thread_pool_settings = [
            f"      - thread_pool.search.size={optimal_settings['thread_pools']['search']}",
            "      - thread_pool.search.queue_size=2000",
            f"      - thread_pool.write.size={optimal_settings['thread_pools']['write']}",
            "      - thread_pool.write.queue_size=2000",
            f"      - thread_pool.get.size={optimal_settings['thread_pools']['get']}",
            "      - thread_pool.get.queue_size=1000"
        ]

    compose_content = f"""# Docker Compose for {node['name']} - {cluster_name}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Elasticsearch Version: {es_version}
{version_comment}
{version_warnings}
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
      
      # ==================== NODE ROLES ({es_version} syntax) ====================
{chr(10).join(version_config['role_settings'])}
      
      # ==================== MEMORY OPTIMIZATION ====================
      - "ES_JAVA_OPTS={jvm_opts}"
      - bootstrap.memory_lock=true
      
      # ==================== DISCOVERY CONFIGURATION ({es_version} syntax) ====================
{chr(10).join(version_config['discovery_settings'])}
      
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
{chr(10).join(thread_pool_settings)}
      
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
      
      # ==================== X-PACK FEATURES ({es_version} syntax) ====================
{chr(10).join(version_config['xpack_settings'])}
      
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
      # **NEW**: Custom JVM options support (recommended by Elastic docs)
      - ./{node['name']}/jvm.options.d:/usr/share/elasticsearch/config/jvm.options.d
      
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
mkdir -p ./{node['name']}/{{data,logs,backups,config,jvm.options.d}}

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

# Create JVM options with container-friendly logging
echo "⚙️ Creating container-optimized jvm.options..."
optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])

# Create custom JVM options file in jvm.options.d directory (recommended approach)
cat > ./{node['name']}/jvm.options.d/container-optimized.options << EOF
# Container-optimized JVM configuration for {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

# Heap size configuration
-Xms{optimal['heap_size']}
-Xmx{optimal['heap_size']}

# GC Configuration
{'-XX:+UseG1GC' if optimal['jvm_settings']['gc_collector'] == 'G1GC' else '-XX:+UseConcMarkSweepGC'}
-XX:MaxGCPauseMillis=200

# Container-friendly logging (all output to stderr)
-Xlog:disable
-Xlog:all=warning:stderr:utctime,level,tags
-Xlog:gc=debug:stderr:utctime

# Memory and error handling
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/usr/share/elasticsearch/data
-XX:ErrorFile=/usr/share/elasticsearch/logs/hs_err_pid%p.log
-XX:+ExitOnOutOfMemoryError

# Performance options
-XX:+UseStringDeduplication
-Djava.awt.headless=true
-Dfile.encoding=UTF-8
EOF

# Also create a legacy jvm.options for backward compatibility
cat > ./{node['name']}/config/jvm.options << EOF
# Legacy JVM configuration for {node['name']} (backup)
-Xms{optimal['heap_size']}
-Xmx{optimal['heap_size']}
{'-XX:+UseG1GC' if optimal['jvm_settings']['gc_collector'] == 'G1GC' else '-XX:+UseConcMarkSweepGC'}
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=data
-XX:ErrorFile=logs/hs_err_pid%p.log
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

### Method 1: Complete Setup (Recommended)
1. **Extract all files** to your deployment directory
2. **Run system initialization** (one-time setup):
   ```bash
   chmod +x init.sh
   ./init.sh
   ```
   This sets up Docker, system limits, permissions, and volumes.

3. **Start individual nodes**:
   ```bash
   chmod +x run-*.sh"""
    
    for node in nodes:
        readme_content += f"""
   ./run-{node['name']}.sh"""
    
    readme_content += f"""
   ```

### Method 2: Legacy Approach
1. **Extract all files** to your deployment directory
2. **Make init scripts executable**: `chmod +x init-*.sh`
3. **Run legacy init scripts**:"""
    
    for node in nodes:
        readme_content += f"""
   - `./init-{node['name']}.sh`"""
    
    readme_content += f"""

### Method 3: Manual Docker Compose
```bash
# Start all nodes manually
{' && '.join([f'docker-compose -f docker-compose-{node["name"]}.yml up -d' for node in nodes])}
```
"""
    
    readme_content += """
## Files Included
"""
    
    # Generate files for each node
    for node in nodes:
        # Individual compose file
        compose_file = generate_individual_docker_compose(node, config)
        cluster_files[f"docker-compose-{node['name']}.yml"] = compose_file
        
        # Individual init script (legacy - for backwards compatibility)
        init_script = generate_init_script(node, config)
        cluster_files[f"init-{node['name']}.sh"] = init_script
        
        # System initialization script (comprehensive setup)
        system_init_script = generate_system_init_script(node, config)
        cluster_files[f"init.sh"] = system_init_script
        
        # Node-specific run script
        run_script = generate_node_run_script(node, config)
        cluster_files[f"run-{node['name']}.sh"] = run_script
        
        # **NEW**: Version-specific JVM options file
        jvm_options_file = generate_jvm_options_file(node, config)
        cluster_files[f"{node['name']}-container-optimized.options"] = jvm_options_file
        
        readme_content += f"""
### {node['name']} Files:
- `docker-compose-{node['name']}.yml` - Complete Docker Compose configuration
- `init-{node['name']}.sh` - Basic initialization script (legacy)
- `run-{node['name']}.sh` - Node-specific run script with health checks
- `{node['name']}-container-optimized.options` - **NEW**: Version-specific JVM options for containers
"""
    
    # Add system-wide scripts
    readme_content += f"""
### System-wide Scripts:
- `init.sh` - Comprehensive system initialization (Docker, permissions, limits)
- `run-<node>.sh` - Individual node run scripts with validation
"""
    
    readme_content += """
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

## **NEW**: Container-Optimized Features

### Version-Specific JVM Options
This generator now includes optimized JVM configurations for container deployments:

- **Elasticsearch 6.x**: Legacy GC logging with stderr output for container compatibility
- **Elasticsearch 7.x**: Modern `-Xlog` syntax with container-friendly logging
- **Elasticsearch 8.x**: Latest JVM optimizations with enhanced container support

### Custom JVM Options Usage
Each node includes a `{node-name}-container-optimized.options` file. To use:

1. **Manual placement** (during setup):
   ```bash
   mkdir -p ./{nodes[0]['name']}/jvm.options.d/
   cp {nodes[0]['name']}-container-optimized.options ./{nodes[0]['name']}/jvm.options.d/
   ```

2. **Automatic mounting**: The Docker Compose files automatically mount the `jvm.options.d` directory

### Container Logging Benefits
- All GC logs output to stderr (container-friendly)
- No log file rotation needed in containers
- Better integration with container orchestrators (Docker, Kubernetes)
- Easier monitoring with centralized logging solutions

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

def generate_system_init_script(node, config):
    """Generate system initialization script for Docker setup, volumes, and permissions"""
    cluster_name = config['cluster_name']
    
    script_content = f"""#!/bin/bash
# System Initialization Script for Elasticsearch Node: {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Cluster: {cluster_name} | Node: {node['name']} | Roles: {', '.join(node['roles'])}

set -e

echo "🚀 System Initialization for Elasticsearch Node: {node['name']}"
echo "📊 Cluster: {cluster_name}"
echo "🎭 Roles: {', '.join(node['roles'])}"
echo "💻 Hardware: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM"
echo "🌐 Network: {node['ip']}:{node['http_port']}"
echo "==============================================="

# Function to check if command exists
command_exists() {{
    command -v "$1" >/dev/null 2>&1
}}

# Check if running as root for system modifications
if [[ $EUID -eq 0 ]]; then
    echo "⚠️  Running as root - will modify system settings"
    ROOT_USER=true
else
    echo "ℹ️  Running as non-root user"
    ROOT_USER=false
fi

# ==================== DOCKER INSTALLATION ====================
echo "🐳 Checking Docker installation..."

if ! command_exists docker; then
    echo "❌ Docker not found. Installing Docker..."
    
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux installation
        if command_exists apt-get; then
            # Ubuntu/Debian
            sudo apt-get update
            sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
            echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            sudo apt-get update
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
        elif command_exists yum; then
            # CentOS/RHEL
            sudo yum install -y yum-utils
            sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
        elif command_exists dnf; then
            # Fedora
            sudo dnf -y install dnf-plugins-core
            sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
        else
            echo "❌ Unsupported Linux distribution. Please install Docker manually."
            exit 1
        fi
        
        # Start and enable Docker
        sudo systemctl start docker
        sudo systemctl enable docker
        
        # Add current user to docker group
        if [[ "$ROOT_USER" == "false" ]]; then
            sudo usermod -aG docker $USER
            echo "⚠️  User added to docker group. Please log out and log back in, or run 'newgrp docker'"
        fi
        
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "❌ macOS detected. Please install Docker Desktop manually from https://docker.com/products/docker-desktop"
        exit 1
    else
        echo "❌ Unsupported operating system. Please install Docker manually."
        exit 1
    fi
else
    echo "✅ Docker is already installed"
fi

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "🔄 Starting Docker service..."
    if [[ "$ROOT_USER" == "true" ]]; then
        systemctl start docker || service docker start
    else
        sudo systemctl start docker || sudo service docker start
    fi
    
    # Wait for Docker to be ready
    echo "⏳ Waiting for Docker to be ready..."
    for i in {{1..30}}; do
        if docker info > /dev/null 2>&1; then
            echo "✅ Docker is ready!"
            break
        fi
        echo "⏳ Waiting... ($i/30)"
        sleep 2
    done
    
    if ! docker info > /dev/null 2>&1; then
        echo "❌ Docker failed to start"
        exit 1
    fi
else
    echo "✅ Docker is running"
fi

# ==================== DOCKER COMPOSE INSTALLATION ====================
echo "🔧 Checking Docker Compose..."

if ! command_exists docker-compose && ! docker compose version > /dev/null 2>&1; then
    echo "❌ Docker Compose not found. Installing..."
    
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Install docker-compose
        sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
        
        # Create symlink for docker compose (new syntax)
        sudo ln -sf /usr/local/bin/docker-compose /usr/local/bin/docker-compose
    else
        echo "❌ Please install Docker Compose manually"
        exit 1
    fi
else
    echo "✅ Docker Compose is available"
fi

# ==================== SYSTEM LIMITS CONFIGURATION ====================
echo "⚙️ Configuring system limits for Elasticsearch..."

# Set vm.max_map_count for Elasticsearch
if [[ "$ROOT_USER" == "true" ]] || sudo -n true 2>/dev/null; then
    echo "🔧 Setting vm.max_map_count=262144..."
    
    # Set for current session
    if [[ "$ROOT_USER" == "true" ]]; then
        sysctl -w vm.max_map_count=262144
    else
        sudo sysctl -w vm.max_map_count=262144
    fi
    
    # Persist the setting
    if ! grep -q "vm.max_map_count=262144" /etc/sysctl.conf 2>/dev/null; then
        if [[ "$ROOT_USER" == "true" ]]; then
            echo "vm.max_map_count=262144" >> /etc/sysctl.conf
        else
            echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
        fi
        echo "✅ vm.max_map_count persisted in /etc/sysctl.conf"
    else
        echo "✅ vm.max_map_count already configured"
    fi
else
    echo "⚠️  Cannot modify system limits (no sudo access). Please run:"
    echo "   sudo sysctl -w vm.max_map_count=262144"
    echo "   echo 'vm.max_map_count=262144' | sudo tee -a /etc/sysctl.conf"
fi

# Configure ulimits for the current user
echo "🔧 Configuring ulimits..."

# Check current limits
echo "📊 Current limits:"
echo "   Max open files: $(ulimit -n)"
echo "   Max processes: $(ulimit -u)"

# Set session limits
ulimit -n 65536 2>/dev/null || echo "⚠️  Could not set ulimit -n"
ulimit -u 32768 2>/dev/null || echo "⚠️  Could not set ulimit -u"

# Configure persistent limits
LIMITS_CONF="/etc/security/limits.conf"
if [[ "$ROOT_USER" == "true" ]] || sudo -n true 2>/dev/null; then
    if [[ -f "$LIMITS_CONF" ]]; then
        echo "🔧 Configuring persistent limits in $LIMITS_CONF..."
        
        # Add limits for elasticsearch user and current user
        CURRENT_USER=$(whoami)
        
        LIMITS_TO_ADD=(
            "elasticsearch soft nofile 65536"
            "elasticsearch hard nofile 65536"
            "elasticsearch soft nproc 32768"
            "elasticsearch hard nproc 32768"
            "$CURRENT_USER soft nofile 65536"
            "$CURRENT_USER hard nofile 65536"
            "$CURRENT_USER soft nproc 32768"
            "$CURRENT_USER hard nproc 32768"
        )
        
        for limit in "${{LIMITS_TO_ADD[@]}}"; do
            if ! grep -q "$limit" "$LIMITS_CONF" 2>/dev/null; then
                if [[ "$ROOT_USER" == "true" ]]; then
                    echo "$limit" >> "$LIMITS_CONF"
                else
                    echo "$limit" | sudo tee -a "$LIMITS_CONF" > /dev/null
                fi
                echo "✅ Added: $limit"
            fi
        done
    fi
else
    echo "⚠️  Cannot modify $LIMITS_CONF (no sudo access)"
fi

# ==================== DIRECTORY CREATION ====================
echo "📁 Creating directories for {node['name']}..."

# Create directory structure
DIRS=(
    "./{node['name']}"
    "./{node['name']}/data"
    "./{node['name']}/logs"
    "./{node['name']}/backups"
    "./{node['name']}/config"
    "./{node['name']}/plugins"
)

for dir in "${{DIRS[@]}}"; do
    if [[ ! -d "$dir" ]]; then
        mkdir -p "$dir"
        echo "✅ Created: $dir"
    else
        echo "ℹ️  Exists: $dir"
    fi
done

# ==================== PERMISSIONS CONFIGURATION ====================
echo "🔐 Setting correct permissions..."

# Elasticsearch runs as user 1000:1000 in Docker
ES_UID=1000
ES_GID=1000

# Set ownership and permissions
if [[ "$ROOT_USER" == "true" ]] || sudo -n true 2>/dev/null; then
    echo "🔧 Setting ownership to $ES_UID:$ES_GID..."
    
    if [[ "$ROOT_USER" == "true" ]]; then
        chown -R $ES_UID:$ES_GID ./{node['name']}/
        chmod -R 755 ./{node['name']}/
        chmod -R 777 ./{node['name']}/data
        chmod -R 777 ./{node['name']}/logs
        chmod -R 777 ./{node['name']}/backups
    else
        sudo chown -R $ES_UID:$ES_GID ./{node['name']}/
        sudo chmod -R 755 ./{node['name']}/
        sudo chmod -R 777 ./{node['name']}/data
        sudo chmod -R 777 ./{node['name']}/logs
        sudo chmod -R 777 ./{node['name']}/backups
    fi
    
    echo "✅ Permissions set successfully"
else
    echo "⚠️  Cannot set ownership (no sudo access). Please run:"
    echo "   sudo chown -R 1000:1000 ./{node['name']}/"
    echo "   sudo chmod -R 755 ./{node['name']}/"
    echo "   sudo chmod -R 777 ./{node['name']}/{{data,logs,backups}}"
fi

# ==================== VALIDATION ====================
echo "🔍 Validating setup..."

# Check Docker
if docker info > /dev/null 2>&1; then
    echo "✅ Docker: Ready"
else
    echo "❌ Docker: Not ready"
fi

# Check Docker Compose
if command_exists docker-compose || docker compose version > /dev/null 2>&1; then
    echo "✅ Docker Compose: Available"
else
    echo "❌ Docker Compose: Not available"
fi

# Check directories
all_dirs_exist=true
for dir in "${{DIRS[@]}}"; do
    if [[ ! -d "$dir" ]]; then
        echo "❌ Directory missing: $dir"
        all_dirs_exist=false
    fi
done

if [[ "$all_dirs_exist" == "true" ]]; then
    echo "✅ Directories: All present"
fi

# Check system limits
current_max_map_count=$(sysctl vm.max_map_count 2>/dev/null | awk '{{print $3}}' || echo "unknown")
  if [[ "$current_max_map_count" -ge 262144 ]] 2>/dev/null; then
      echo "✅ vm.max_map_count: $current_max_map_count (sufficient)"
  else
      echo "⚠️  vm.max_map_count: $current_max_map_count (should be 262144+)"
fi

echo ""
echo "==============================================="
echo "✅ System initialization for {node['name']} completed!"
echo ""
echo "📋 Next steps:"
echo "1. Run the node-specific run script: ./run-{node['name']}.sh"
echo "2. Check logs: docker logs {node['name']}"
echo "3. Monitor health: curl http://{node['ip']}:{node['http_port']}/_cluster/health"
echo ""
echo "⚠️  If you see permission errors, you may need to run:"
echo "   sudo chown -R 1000:1000 ./{node['name']}/"
echo "==============================================="
"""
    
    return script_content

def generate_node_run_script(node, config):
    """Generate node-specific run script for starting Elasticsearch"""
    cluster_name = config['cluster_name']
    
    script_content = f"""#!/bin/bash
# Run Script for Elasticsearch Node: {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Cluster: {cluster_name} | Node: {node['name']} | Roles: {', '.join(node['roles'])}

set -e

echo "🚀 Starting Elasticsearch Node: {node['name']}"
echo "📊 Cluster: {cluster_name}"
echo "🎭 Roles: {', '.join(node['roles'])}"
echo "💻 Hardware: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM"
echo "🌐 Network: {node['ip']}:{node['http_port']}"
echo "==============================================="

# Function to check if command exists
command_exists() {{
    command -v "$1" >/dev/null 2>&1
}}

# ==================== PRE-FLIGHT CHECKS ====================
echo "🔍 Running pre-flight checks..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first or run init script."
    exit 1
fi
echo "✅ Docker is running"

# Check if Docker Compose is available
if ! command_exists docker-compose && ! docker compose version > /dev/null 2>&1; then
    echo "❌ Docker Compose not found. Please install it or run init script."
    exit 1
fi
echo "✅ Docker Compose is available"

# Check if compose file exists
COMPOSE_FILE="docker-compose-{node['name']}.yml"
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "❌ Docker Compose file not found: $COMPOSE_FILE"
    echo "ℹ️  Please ensure you've extracted all files from the cluster package."
    exit 1
fi
echo "✅ Docker Compose file found: $COMPOSE_FILE"

# ==================== DIRECTORY VALIDATION ====================
echo "📁 Validating directories for {node['name']}..."

# Required directories
DIRS=(
    "./{node['name']}"
    "./{node['name']}/data"
    "./{node['name']}/logs"
    "./{node['name']}/backups"
    "./{node['name']}/config"
)

missing_dirs=false
for dir in "${{DIRS[@]}}"; do
    if [[ ! -d "$dir" ]]; then
        echo "⚠️  Creating missing directory: $dir"
        mkdir -p "$dir"
        missing_dirs=true
    fi
done

if [[ "$missing_dirs" == "true" ]]; then
    echo "ℹ️  Some directories were missing and have been created."
    echo "ℹ️  Consider running the init script for proper setup."
fi

# ==================== PERMISSIONS CHECK ====================
echo "🔐 Checking permissions..."

# Check if directories are writable
for dir in "./{node['name']}/data" "./{node['name']}/logs" "./{node['name']}/backups"; do
    if [[ ! -w "$dir" ]]; then
        echo "⚠️  Directory not writable: $dir"
        echo "🔧 Attempting to fix permissions..."
        
        if sudo -n true 2>/dev/null; then
            sudo chown -R 1000:1000 ./{node['name']}/
            sudo chmod -R 755 ./{node['name']}/
            sudo chmod -R 777 ./{node['name']}/data ./{node['name']}/logs ./{node['name']}/backups
            echo "✅ Permissions fixed"
        else
            echo "❌ Cannot fix permissions. Please run:"
            echo "   sudo chown -R 1000:1000 ./{node['name']}/"
            echo "   sudo chmod -R 777 ./{node['name']}/{{data,logs,backups}}"
            echo "   Or run the init script with appropriate privileges."
        fi
    fi
done

# ==================== SYSTEM LIMITS CHECK ====================
echo "⚙️ Checking system limits..."

# Check vm.max_map_count
current_max_map_count=$(sysctl vm.max_map_count 2>/dev/null | awk '{{print $3}}' || echo "0")
if [[ "$current_max_map_count" -lt 262144 ]] 2>/dev/null; then
    echo "⚠️  vm.max_map_count is $current_max_map_count (should be 262144+)"
    echo "🔧 Attempting to fix..."
    
    if sudo -n true 2>/dev/null; then
        sudo sysctl -w vm.max_map_count=262144
        echo "✅ vm.max_map_count set to 262144"
    else
        echo "❌ Cannot set vm.max_map_count. Please run:"
        echo "   sudo sysctl -w vm.max_map_count=262144"
        echo "   Or run the init script with appropriate privileges."
    fi
else
    echo "✅ vm.max_map_count: $current_max_map_count (sufficient)"
fi

# Check ulimits
current_nofile=$(ulimit -n)
if [[ "$current_nofile" -lt 65536 ]] 2>/dev/null; then
    echo "⚠️  Current ulimit -n: $current_nofile (recommended: 65536+)"
    ulimit -n 65536 2>/dev/null || echo "❌ Could not increase file descriptor limit"
else
    echo "✅ File descriptor limit: $current_nofile (sufficient)"
fi

# ==================== CONTAINER MANAGEMENT ====================
echo "🐳 Managing {node['name']} container..."

# Check if container already exists
if docker ps -a --format "table {{{{.Names}}}}" | grep -q "^{node['name']}$"; then
    echo "ℹ️  Container {node['name']} already exists"
    
    # Check if it's running
    if docker ps --format "table {{{{.Names}}}}" | grep -q "^{node['name']}$"; then
        echo "ℹ️  Container {node['name']} is already running"
        
        echo "🔄 Checking container health..."
        for i in {{1..10}}; do
            if curl -f http://{node['ip']}:{node['http_port']}/_cluster/health > /dev/null 2>&1; then
                echo "✅ {node['name']} is healthy and ready!"
                break
            fi
            echo "⏳ Waiting for health check... ($i/10)"
            sleep 3
        done
        
        echo "📊 Container status:"
        docker ps --filter "name={node['name']}" --format "table {{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}"
        
        echo ""
        echo "🔗 Useful commands:"
        echo "   View logs: docker logs {node['name']}"
        echo "   Follow logs: docker logs -f {node['name']}"
        echo "   Stop: docker stop {node['name']}"
        echo "   Restart: docker restart {node['name']}"
        
        exit 0
    else
        echo "🔄 Starting existing container..."
        docker start {node['name']}
    fi
else
    echo "🚀 Creating and starting new container..."
    
    # Determine docker-compose command
    if command_exists docker-compose; then
        COMPOSE_CMD="docker-compose"
    else
        COMPOSE_CMD="docker compose"
    fi
    
    # Start the container
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d
fi

# ==================== HEALTH CHECK ====================
echo "🏥 Performing health check..."

echo "⏳ Waiting for {node['name']} to be ready..."
for i in {{1..60}}; do
    if curl -f http://{node['ip']}:{node['http_port']}/_cluster/health > /dev/null 2>&1; then
        echo "✅ {node['name']} is healthy and ready!"
        break
    fi
    
    if [[ $i -eq 60 ]]; then
        echo "❌ {node['name']} failed to become ready after 5 minutes"
        echo "📋 Troubleshooting steps:"
        echo "   1. Check logs: docker logs {node['name']}"
        echo "   2. Check container status: docker ps -a"
        echo "   3. Verify permissions on data directories"
        echo "   4. Check system resources (disk space, memory)"
        exit 1
    fi
    
    echo "⏳ Waiting for {node['name']} to be ready... ($i/60)"
    sleep 5
done

# ==================== STATUS REPORT ====================
echo ""
echo "==============================================="
echo "📊 Node Status Report: {node['name']}"
echo "==============================================="

# Container status
echo "🐳 Container Status:"
docker ps --filter "name={node['name']}" --format "table {{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}"

# Elasticsearch cluster health
echo ""
echo "🏥 Cluster Health:"
curl -s http://{node['ip']}:{node['http_port']}/_cluster/health?pretty 2>/dev/null || echo "❌ Could not retrieve cluster health"

# Node info
echo ""
echo "🖥️ Node Information:"
curl -s http://{node['ip']}:{node['http_port']}/_nodes/{node['name']}?pretty 2>/dev/null | grep -E '"name"|"version"|"roles"' || echo "❌ Could not retrieve node info"

echo ""
echo "==============================================="
echo "✅ {node['name']} is running successfully!"
echo ""
echo "🔗 Useful URLs:"
echo "   Health: http://{node['ip']}:{node['http_port']}/_cluster/health"
echo "   Stats:  http://{node['ip']}:{node['http_port']}/_nodes/stats"
echo "   Cat:    http://{node['ip']}:{node['http_port']}/_cat/nodes?v"
echo ""
echo "📋 Management Commands:"
echo "   View logs:    docker logs {node['name']}"
echo "   Follow logs:  docker logs -f {node['name']}"
echo "   Stop node:    docker stop {node['name']}"
echo "   Restart node: docker restart {node['name']}"
echo "   Remove node:  docker-compose -f docker-compose-{node['name']}.yml down"
echo "==============================================="
"""
    
    return script_content

def generate_jvm_options_file(node, config):
    """Generate version-specific JVM options file for container deployment"""
    es_version = config['es_version']
    version_major = int(es_version.split('.')[0])
    optimal_settings = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
    
    # Base heap configuration
    jvm_content = f"""# Container-optimized JVM configuration for {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Elasticsearch Version: {es_version}
# Node Roles: {', '.join(node['roles'])}
# Hardware: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM

# ==================== HEAP CONFIGURATION ====================
-Xms{optimal_settings['heap_size']}
-Xmx{optimal_settings['heap_size']}

# ==================== GC CONFIGURATION ===================="""
    
    # Version-specific GC and logging configuration
    if version_major == 6:
        # Elasticsearch 6.x - Legacy JVM options
        if optimal_settings['jvm_settings']['gc_collector'] == 'G1GC':
            jvm_content += f"""
-XX:+UseG1GC
-XX:MaxGCPauseMillis=250
-XX:G1HeapRegionSize=16m

# Legacy GC logging for v6 (container-friendly)
-XX:+PrintGC
-XX:+PrintGCDetails
-XX:+PrintGCTimeStamps
-XX:+PrintGCApplicationStoppedTime
-Xloggc:/dev/stderr"""
        else:
            jvm_content += f"""
-XX:+UseConcMarkSweepGC
-XX:CMSInitiatingOccupancyFraction={optimal_settings['jvm_settings']['cms_initiating_occupancy_fraction']}
-XX:+UseCMSInitiatingOccupancyOnly
-XX:+CMSParallelRemarkEnabled
-XX:+UseCMSCompactAtFullCollection

# Legacy GC logging for v6 (container-friendly)
-XX:+PrintGC
-XX:+PrintGCDetails
-XX:+PrintGCTimeStamps
-Xloggc:/dev/stderr"""
            
    elif version_major == 7:
        # Elasticsearch 7.x - Modern JVM with container logging
        jvm_content += f"""
-XX:+UseG1GC
-XX:MaxGCPauseMillis=200
-XX:G1HeapRegionSize=16m
-XX:+UseStringDeduplication

# Modern GC logging for v7 (container-friendly stderr output)
-Xlog:disable
-Xlog:all=warning:stderr:utctime,level,tags
-Xlog:gc=debug:stderr:utctime"""
        
    elif version_major == 8:
        # Elasticsearch 8.x - Latest JVM optimizations
        jvm_content += f"""
-XX:+UseG1GC
-XX:MaxGCPauseMillis=200
-XX:G1HeapRegionSize=16m
-XX:+UseStringDeduplication

# Modern GC logging for v8 (container-friendly stderr output)
-Xlog:disable
-Xlog:all=warning:stderr:utctime,level,tags
-Xlog:gc=debug:stderr:utctime

# Enhanced v8 options
-XX:+UnlockExperimentalVMOptions
-XX:+UseShenandoahGC  # Available in v8+ for low-latency scenarios"""
    
    # Common memory and error handling options for all versions
    jvm_content += f"""

# ==================== MEMORY & ERROR HANDLING ====================
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/usr/share/elasticsearch/data
-XX:ErrorFile=/usr/share/elasticsearch/logs/hs_err_pid%p.log
-XX:+ExitOnOutOfMemoryError"""
    
    # Add crash handling for v8+
    if version_major >= 8:
        jvm_content += """
-XX:+CrashOnOutOfMemoryError"""
    
    # Common JVM options
    jvm_content += f"""

# ==================== GENERAL JVM OPTIONS ====================
-Djava.awt.headless=true
-Dfile.encoding=UTF-8
-Djava.security.policy=all.policy
-Dlog4j2.disable.jmx=true

# ==================== PERFORMANCE TUNING ====================
# Optimize for container environments
-XX:+AlwaysPreTouch
-XX:+UseLargePages
-XX:+UseTransparentHugePages

# Network and DNS optimization
-Djava.net.preferIPv4Stack=true
-Djna.nosys=true

# Security hardening
-Djava.io.tmpdir=/tmp
-Djava.security.manager=default"""
    
    # Add version-specific performance options
    if version_major >= 7:
        jvm_content += """

# Modern JVM performance options (v7+)
-XX:+UseCompressedOops
-XX:+UseCompressedClassPointers"""
    
    if version_major >= 8:
        jvm_content += """

# Latest JVM performance options (v8+)
-XX:+EnableJVMCI
-XX:+UseJVMCICompiler"""
    
    return jvm_content

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
                                    
                                    # Tabs for Summary and Details
                                    tab1, tab2 = st.tabs(["📊 Quick Summary", "🎯 Details"])
                                    
                                    # Calculate optimal settings for tabs
                                    optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
                                    node_type = "Master-Only" if node['roles'] == ['master'] else "Data Node" if 'data' in node['roles'] else "Mixed"
                                    
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
        
        # Version-specific information
        with st.expander("🔧 **Version-Specific Configuration Guide**", expanded=False):
            current_version = st.session_state.cluster_config['es_version']
            version_major = int(current_version.split('.')[0])
            
            st.markdown(f"""
            **Your Selected Version: {current_version}** (v{version_major}.x series)
            
            ### Key Configuration Differences by Version:
            """)
            
            if version_major == 6:
                st.info("""
                **🔹 Elasticsearch 6.x Configuration:**
                - **Discovery**: Uses `discovery.zen.ping.unicast.hosts` for cluster discovery
                - **Split-brain**: Uses `discovery.zen.minimum_master_nodes` 
                - **Node roles**: Boolean flags (`node.master=true`, `node.data=true`, `node.ingest=true`)
                - **X-Pack**: Basic syntax (⚠️ ILM not available in v6)
                - **Security**: Manual configuration required
                
                **Example settings for your cluster:**
                ```yaml
                discovery.zen.ping.unicast.hosts=els01.example.com,els02.example.com
                discovery.zen.minimum_master_nodes=2
                node.master=true
                node.data=true
                xpack.security.enabled=false
                ```
                """)
            elif version_major == 7:
                st.success("""
                **🔹 Elasticsearch 7.x Configuration:**
                - **Discovery**: Hybrid approach - zen discovery (v7.0-7.6) or seed hosts (v7.7+)
                - **Bootstrap**: Uses `cluster.initial_master_nodes` for first startup
                - **Node roles**: Legacy boolean flags (backward compatible)
                - **X-Pack**: Full support including ILM
                - **Security**: Optional, disabled by default
                
                **Example settings for your cluster:**
                ```yaml
                discovery.seed_hosts=["els01.example.com:9300", "els02.example.com:9300"]
                cluster.initial_master_nodes=["els01", "els02"]
                node.master=true
                node.data=true
                xpack.ilm.enabled=true
                ```
                """)
            elif version_major == 8:
                st.success("""
                **🔹 Elasticsearch 8.x Configuration:**
                - **Discovery**: Modern `discovery.seed_hosts` configuration
                - **Bootstrap**: Uses `cluster.initial_master_nodes` for initialization
                - **Node roles**: New array syntax (`node.roles=["master", "data", "ingest"]`)
                - **X-Pack**: Enhanced with explicit security settings
                - **Security**: ⚠️ Enabled by default (explicitly disabled for ease of use)
                
                **Example settings for your cluster:**
                ```yaml
                discovery.seed_hosts=["els01.example.com:9300", "els02.example.com:9300"]
                cluster.initial_master_nodes=["els01", "els02"]
                node.master=true
                node.data=true
                xpack.security.enabled=false
                ```
                """)
            
            st.markdown(f"""
            ### 🎯 Generated Configuration Benefits:
            - ✅ **Version-appropriate syntax** - No compatibility issues
            - ✅ **Split-brain prevention** - Proper master node calculation
            - ✅ **Discovery optimization** - Uses best practices for v{version_major}
            - ✅ **Security settings** - Appropriate defaults for your version
            - ✅ **Performance tuning** - Optimized for your hardware specs
            """)
        
        if len(st.session_state.cluster_config['nodes']) == 0:
            st.warning("⚠️ Please configure at least one node in the Node Configuration tab.")
        else:
            # Validation
            current_version = st.session_state.cluster_config['es_version']
            version_major = int(current_version.split('.')[0])
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
                
            # Version-specific validation and warnings
            version_warnings = []
            hardware_warnings = []
            
            # Version-specific checks
            if version_major == 6:
                # Check for ILM in v6 (not supported)
                if st.session_state.cluster_config['xpack_settings'].get('ilm', False):
                    version_warnings.append("⚠️ ILM is not available in Elasticsearch 6.x - will be ignored in generated config")
                    
            elif version_major == 8:
                # Check security settings for v8
                if not st.session_state.cluster_config['xpack_settings'].get('security', False):
                    version_warnings.append("ℹ️ Security explicitly disabled for v8.x (normally enabled by default)")
            
            # Hardware recommendations for all versions
            for node in st.session_state.cluster_config['nodes']:
                if 'data' in node['roles'] and node['ram_gb'] < 8:
                    hardware_warnings.append(f"⚠️ {node['name']}: Data nodes should have at least 8GB RAM (currently {node['ram_gb']}GB)")
                if 'master' in node['roles'] and node['cpu_cores'] < 2:
                    hardware_warnings.append(f"⚠️ {node['name']}: Master nodes should have at least 2 CPU cores (currently {node['cpu_cores']})")
                if node['ram_gb'] > 64:
                    hardware_warnings.append(f"💡 {node['name']}: Very high RAM ({node['ram_gb']}GB) - ensure heap size doesn't exceed 32GB")
            
            # Display validation results
            if validation_issues:
                st.error("**❌ Validation Issues:**\n" + "\n".join(validation_issues))
            elif version_warnings or hardware_warnings:
                all_warnings = version_warnings + hardware_warnings
                st.warning("**⚠️ Recommendations & Warnings:**\n" + "\n".join(all_warnings))
                st.info(f"✅ Configuration is valid for Elasticsearch {current_version} but please review the warnings above")
            else:
                st.success(f"✅ Configuration validated successfully for Elasticsearch {current_version}!")
                
                # Generate cluster files
                st.info("🚀 **Enhanced Package**: Individual Docker Compose files + comprehensive init.sh and run scripts for each node!")
                
                with st.expander("📋 **What's Included in the Package**", expanded=False):
                    st.markdown("""
                    **System-wide Scripts:**
                    - `init.sh` - Comprehensive system initialization (Docker, permissions, limits)
                    - `README.md` - Complete documentation and usage instructions
                    
                    **Per-Node Files:**
                    - `docker-compose-<node>.yml` - Complete Docker Compose with integrated settings
                    - `run-<node>.sh` - Node-specific run script with health checks and validation
                    - `init-<node>.sh` - Legacy initialization script (backwards compatibility)
                    
                    **Key Features:**
                    - ✅ Docker installation and setup
                    - ✅ System limits configuration (vm.max_map_count, ulimits)
                    - ✅ Directory creation and permissions
                    - ✅ Pre-flight checks and validation
                    - ✅ Health monitoring and status reporting
                    - ✅ Automatic hostname resolution between nodes
                    """)
                
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
                                
                                # Create tabs for different file types
                                file_tab1, file_tab2, file_tab3, file_tab4 = st.tabs(["🐳 Docker Compose", "🚀 Run Script", "⚙️ System Init", "📜 Legacy Init"])
                                
                                with file_tab1:
                                    st.subheader("🐳 Docker Compose File")
                                    compose_content = generate_individual_docker_compose(node, st.session_state.cluster_config)
                                    st.code(compose_content, language='yaml')
                                    st.info("✅ **All settings integrated**: Environment variables, X-Pack features, and configurations are included directly in the Docker Compose file.")
                                
                                with file_tab2:
                                    st.subheader(f"🚀 Run Script: run-{node['name']}.sh")
                                    st.markdown("**Purpose**: Start this specific node with comprehensive health checks and validation")
                                    run_content = generate_node_run_script(node, st.session_state.cluster_config)
                                    st.code(run_content, language='bash')
                                    st.success("✅ **Features**: Pre-flight checks, directory validation, permissions check, health monitoring")
                                
                                with file_tab3:
                                    st.subheader("⚙️ System Initialization: init.sh")
                                    st.markdown("**Purpose**: One-time system setup for Docker, permissions, and system limits")
                                    init_content = generate_system_init_script(node, st.session_state.cluster_config)
                                    st.code(init_content, language='bash')
                                    st.info("✅ **Features**: Docker installation, system limits (vm.max_map_count), ulimits, directory creation, permissions")
                                
                                with file_tab4:
                                    st.subheader(f"📜 Legacy Init: init-{node['name']}.sh")
                                    st.markdown("**Purpose**: Basic node initialization (kept for backwards compatibility)")
                                    legacy_init_content = generate_init_script(node, st.session_state.cluster_config)
                                    st.code(legacy_init_content, language='bash')
                                    st.warning("⚠️ **Legacy**: Use the System Init and Run scripts for better functionality")
                                
                                # Show file summary
                                st.markdown("---")
                                st.info(f"""
                                📦 **Complete Package for {node['name']}**:
                                - `docker-compose-{node['name']}.yml` - Complete Docker Compose with integrated settings
                                - `run-{node['name']}.sh` - Node-specific run script with validation and health checks
                                - `init.sh` - System-wide initialization (Docker, permissions, limits)
                                - `init-{node['name']}.sh` - Legacy initialization script
                                
                                **Recommended workflow**:
                                1. Run `init.sh` once for system setup
                                2. Run `run-{node['name']}.sh` to start this node
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