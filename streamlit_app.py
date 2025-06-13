import streamlit as st
import json
import yaml
from datetime import datetime
import zipfile
import io
from typing import Dict, List, Any
import streamlit_mermaid as stmd

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

def calculate_production_memory_limits(total_ram_gb: int, environment: str = "production"):
    """
    Calculate production-grade Docker memory limits for dedicated Elasticsearch servers
    
    Formula: 
    - Heap = min(Total_RAM × 0.50, 31GB)  # ES Best Practice: 50% of system RAM
    - Container = Heap + Off-heap buffer + OS Reserve
    
    Args:
        total_ram_gb: Total system RAM in GB
        environment: production, staging, or development
    
    Returns:
        dict with container_limit_gb, heap_size_gb, os_reserve_gb, etc.
    """
    # Elasticsearch Heap Size Formula: min(Total_RAM × 0.50, 31GB)
    # This follows ES best practice: heap should be 50% of SYSTEM RAM, not container
    heap_size_gb = min(total_ram_gb * 0.50, 31)
    
    # OS Reserve for DEDICATED Elasticsearch servers (minimal requirements)
    if total_ram_gb <= 16:
        os_reserve_gb = 1.0      # Minimal for small dedicated servers
    elif total_ram_gb <= 64:
        os_reserve_gb = 1.5      # Adequate for medium dedicated servers  
    else:
        os_reserve_gb = 2.0      # Conservative for large dedicated servers
    
    # Container needs: Heap + Off-heap buffer (for file system cache, etc.)
    # For dedicated servers, we can be more aggressive
    container_limit_gb = total_ram_gb - os_reserve_gb
    
    # Calculate off-heap memory (remaining container memory after heap)
    off_heap_gb = container_limit_gb - heap_size_gb
    
    # Safety factor for monitoring (not used in calculation, just for reference)
    safety_factors = {
        'development': 0.85,
        'staging': 0.90,
        'production': 0.96    # More aggressive for dedicated servers
    }
    safety_factor = safety_factors.get(environment.lower(), 0.96)
    
    # Round to reasonable values
    container_limit_gb = round(container_limit_gb, 1)
    heap_size_gb = round(heap_size_gb, 1)
    off_heap_gb = round(off_heap_gb, 1)
    
    return {
        'container_limit_gb': container_limit_gb,
        'heap_size_gb': heap_size_gb,
        'os_reserve_gb': os_reserve_gb,
        'safety_factor': safety_factor,
        'usable_ram_gb': round(total_ram_gb - os_reserve_gb, 1),
        'off_heap_gb': off_heap_gb
    }

def calculate_optimal_settings(cpu_cores: int, ram_gb: int, node_roles: list):
    """Calculate optimal Elasticsearch settings based on hardware with production-grade memory limits"""
    
    # Calculate production-grade memory limits
    memory_limits = calculate_production_memory_limits(ram_gb, "production")
    
    # Use calculated heap size instead of simple 50% rule
    heap_gb = memory_limits['heap_size_gb']
    
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
        'production_memory_limits': memory_limits,  # NEW: Production-grade memory calculations
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

def generate_development_docker_compose(config):
    """Generate single Docker Compose file for development with all nodes"""
    cluster_name = config['cluster_name']
    es_version = config['es_version']
    nodes = config['nodes']
    
    # Get version-specific configuration for the first node (all nodes will use same base config)
    version_config = get_version_specific_settings(es_version, nodes[0], nodes, cluster_name, config)
    
    compose_content = f"""# Development Docker Compose for {cluster_name}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Elasticsearch Version: {es_version}
# Mode: Development (All nodes in single file)
# Nodes: {len(nodes)} ({', '.join([node['name'] for node in nodes])})

version: '3.8'

services:"""
    
    # Generate services for each node
    for node in nodes:
        # Get version-specific configuration for this node
        node_version_config = get_version_specific_settings(es_version, node, nodes, cluster_name, config)
        optimal_settings = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
        
        # Build JVM options
        base_jvm_opts = f"-Xms{optimal_settings['heap_size']} -Xmx{optimal_settings['heap_size']}"
        
        # Version-specific JVM options
        if node_version_config['version_major'] == 6:
            if optimal_settings['jvm_settings']['gc_collector'] == 'G1GC':
                version_specific_opts = [
                    '-XX:+UseG1GC',
                    '-XX:MaxGCPauseMillis=250',
                    '-XX:G1HeapRegionSize=16m',
                    '-XX:+PrintGC',
                    '-XX:+PrintGCDetails',
                    '-XX:+PrintGCTimeStamps',
                    '-Xloggc:/dev/stderr'
                ]
            else:
                version_specific_opts = [
                    '-XX:+UseConcMarkSweepGC',
                    f'-XX:CMSInitiatingOccupancyFraction={optimal_settings["jvm_settings"]["cms_initiating_occupancy_fraction"]}',
                    '-XX:+UseCMSInitiatingOccupancyOnly',
                    '-XX:+PrintGC',
                    '-XX:+PrintGCDetails',
                    '-Xloggc:/dev/stderr'
                ]
            
            version_specific_opts.extend([
                '-XX:+HeapDumpOnOutOfMemoryError',
                '-XX:HeapDumpPath=/usr/share/elasticsearch/data',
                '-Djava.awt.headless=true',
                '-Dfile.encoding=UTF-8'
            ])
        
        elif node_version_config['version_major'] == 7:
            version_specific_opts = [
                '-XX:+UseG1GC',
                '-XX:MaxGCPauseMillis=200',
                '-XX:G1HeapRegionSize=16m',
                '-Xlog:disable',
                '-Xlog:all=warning:stderr:utctime,level,tags',
                '-Xlog:gc=debug:stderr:utctime',
                '-XX:+HeapDumpOnOutOfMemoryError',
                '-XX:HeapDumpPath=/usr/share/elasticsearch/data',
                '-XX:+ExitOnOutOfMemoryError',
                '-Djava.awt.headless=true',
                '-Dfile.encoding=UTF-8'
            ]
        
        elif node_version_config['version_major'] == 8:
            version_specific_opts = [
                '-XX:+UseG1GC',
                '-XX:MaxGCPauseMillis=200',
                '-XX:G1HeapRegionSize=16m',
                '-XX:+UseStringDeduplication',
                '-Xlog:disable',
                '-Xlog:all=warning:stderr:utctime,level,tags',
                '-Xlog:gc=debug:stderr:utctime',
                '-XX:+HeapDumpOnOutOfMemoryError',
                '-XX:HeapDumpPath=/usr/share/elasticsearch/data',
                '-XX:+ExitOnOutOfMemoryError',
                '-XX:+CrashOnOutOfMemoryError',
                '-Djava.awt.headless=true',
                '-Dfile.encoding=UTF-8'
            ]
        
        # Build complete JVM options string
        jvm_opts = f"{base_jvm_opts} " + " ".join(version_specific_opts)
        
        # For development mode, use container names for discovery instead of hostnames
        # Update discovery settings to use container names
        dev_discovery_settings = []
        for setting in node_version_config['discovery_settings']:
            if 'discovery.seed_hosts' in setting:
                # Replace hostnames with container names for development
                container_names = ','.join([f'"{n["name"]}:9300"' for n in nodes])
                dev_discovery_settings.append(f"      - discovery.seed_hosts=[{container_names}]")
            elif 'discovery.zen.ping.unicast.hosts' in setting:
                # Replace hostnames with container names for development
                container_names = ','.join([n['name'] for n in nodes])
                dev_discovery_settings.append(f"      - discovery.zen.ping.unicast.hosts={container_names}")
            else:
                dev_discovery_settings.append(setting)
        
        # Version-specific thread pool settings
        thread_pool_settings = []
        if node_version_config['version_major'] <= 7:
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
            thread_pool_settings = [
                f"      - thread_pool.search.size={optimal_settings['thread_pools']['search']}",
                "      - thread_pool.search.queue_size=2000",
                f"      - thread_pool.write.size={optimal_settings['thread_pools']['write']}",
                "      - thread_pool.write.queue_size=2000",
                f"      - thread_pool.get.size={optimal_settings['thread_pools']['get']}",
                "      - thread_pool.get.queue_size=1000"
            ]
        
        compose_content += f"""
  {node['name']}:
    image: docker.elastic.co/elasticsearch/elasticsearch:{es_version}
    container_name: {node['name']}
    hostname: {node['name']}
    environment:
      # ==================== CLUSTER CONFIGURATION ====================
      - cluster.name={cluster_name}
      - node.name={node['name']}
      
      # ==================== NODE ROLES ({es_version} syntax) ====================
{chr(10).join(node_version_config['role_settings'])}
      
      # ==================== MEMORY OPTIMIZATION ====================
      - "ES_JAVA_OPTS={jvm_opts}"
      - bootstrap.memory_lock=true
      
      # ==================== DISCOVERY CONFIGURATION (Development Mode) ====================
{chr(10).join(dev_discovery_settings)}
      
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
{chr(10).join(node_version_config['xpack_settings'])}
      
      # ==================== NETWORK CONFIGURATION (Development Mode) ====================
      - network.host=0.0.0.0
      - http.port=9200
      - transport.tcp.port=9300
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
      
    networks:
      - elasticsearch-net
      
    ulimits:
      memlock:
        soft: -1
        hard: -1
      nofile:
        soft: 65536
        hard: 65536
      
    # ==================== RESOURCE LIMITS (Production-Grade Formula) ====================
    # Original Request: {node['ram_gb']}GB | Production Limit: {optimal_settings['production_memory_limits']['container_limit_gb']}GB
    # OS Reserve: {optimal_settings['production_memory_limits']['os_reserve_gb']}GB | Safety Factor: {optimal_settings['production_memory_limits']['safety_factor']} | Heap: {optimal_settings['production_memory_limits']['heap_size_gb']}GB
    mem_limit: {optimal_settings['production_memory_limits']['container_limit_gb']}g
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
        - subnet: 172.21.0.0/16

# Development mode volumes (optional - uncomment if you want persistent volumes)
# volumes:"""
    
    for node in nodes:
        compose_content += f"""
#   {node['name']}_data:
#   {node['name']}_logs:"""
    
    return compose_content

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
      
    # ==================== RESOURCE LIMITS (Production-Grade Formula) ====================
    # Original Request: {node['ram_gb']}GB | Production Limit: {optimal_settings['production_memory_limits']['container_limit_gb']}GB
    # OS Reserve: {optimal_settings['production_memory_limits']['os_reserve_gb']}GB | Safety Factor: {optimal_settings['production_memory_limits']['safety_factor']} | Heap: {optimal_settings['production_memory_limits']['heap_size_gb']}GB
    mem_limit: {optimal_settings['production_memory_limits']['container_limit_gb']}g
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
      
    networks:
      - elasticsearch-net
      
    ulimits:
      memlock:
        soft: -1
        hard: -1
      nofile:
        soft: 65536
        hard: 65536
      
    # ==================== RESOURCE LIMITS (Production-Grade Formula) ====================
    # Original Request: {node['ram_gb']}GB | Production Limit: {optimal_settings['production_memory_limits']['container_limit_gb']}GB
    # OS Reserve: {optimal_settings['production_memory_limits']['os_reserve_gb']}GB | Safety Factor: {optimal_settings['production_memory_limits']['safety_factor']} | Heap: {optimal_settings['production_memory_limits']['heap_size_gb']}GB
    mem_limit: {optimal_settings['production_memory_limits']['container_limit_gb']}g
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

def generate_development_files(config):
    """Generate simplified files for development mode"""
    cluster_files = {}
    nodes = config['nodes']
    cluster_name = config['cluster_name']
    
    # Generate single Docker Compose file
    compose_content = generate_development_docker_compose(config)
    cluster_files["docker-compose.yml"] = compose_content
    
    # Generate simple startup script
    startup_script = f"""#!/bin/bash
# Development Startup Script for {cluster_name}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

set -e

echo "🚀 Starting Elasticsearch Development Cluster: {cluster_name}"
echo "📊 Nodes: {len(nodes)} ({', '.join([node['name'] for node in nodes])})"
echo "🔧 Mode: Development (Single Docker Compose)"
echo "==============================================="

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi

echo "✅ Docker is running"

# Create directories for all nodes
echo "📁 Creating directories..."
"""
    
    for node in nodes:
        startup_script += f"""
mkdir -p ./{node['name']}/{{data,logs,backups,config,jvm.options.d}}"""
    
    startup_script += f"""

# Set basic permissions (if on Linux/Mac)
if [[ "$OSTYPE" != "msys" && "$OSTYPE" != "win32" ]]; then
    echo "🔧 Setting permissions..."
    for dir in {' '.join([f"./{node['name']}" for node in nodes])}; do
        sudo chown -R 1000:1000 "$dir/" 2>/dev/null || echo "⚠️ Could not set ownership for $dir"
        chmod -R 755 "$dir/" 2>/dev/null || echo "⚠️ Could not set permissions for $dir"
    done
fi

# Set vm.max_map_count if possible
if [[ "$OSTYPE" != "msys" && "$OSTYPE" != "win32" ]]; then
    current_max_map_count=$(sysctl vm.max_map_count 2>/dev/null | awk '{{print $3}}' || echo "0")
    if [[ "$current_max_map_count" -lt 262144 ]] 2>/dev/null; then
        echo "🔧 Setting vm.max_map_count..."
        sudo sysctl -w vm.max_map_count=262144 2>/dev/null || echo "⚠️ Could not set vm.max_map_count"
    fi
fi

echo "🚀 Starting all containers..."
docker-compose up -d

echo "⏳ Waiting for cluster to be ready..."
for i in {{1..60}}; do
    if curl -f http://localhost:{nodes[0]['http_port']}/_cluster/health > /dev/null 2>&1; then
        echo "✅ Cluster is ready!"
        break
    fi
    
    if [[ $i -eq 60 ]]; then
        echo "❌ Cluster failed to become ready after 5 minutes"
        echo "📋 Check logs with: docker-compose logs"
        exit 1
    fi
    
    echo "⏳ Waiting for cluster... ($i/60)"
    sleep 5
done

echo ""
echo "==============================================="
echo "✅ Development cluster started successfully!"
echo ""
echo "🔗 Access URLs:"
"""
    
    for i, node in enumerate(nodes):
        startup_script += f"""
echo "   {node['name']}: http://localhost:{node['http_port']}" """
    
    startup_script += f"""

echo ""
echo "📊 Cluster Health:"
curl -s http://localhost:{nodes[0]['http_port']}/_cluster/health?pretty

echo ""
echo "🖥️ Nodes:"
curl -s http://localhost:{nodes[0]['http_port']}/_cat/nodes?v

echo ""
echo "📋 Management Commands:"
echo "   View logs:     docker-compose logs"
echo "   Follow logs:   docker-compose logs -f"
echo "   Stop cluster:  docker-compose down"
echo "   Restart:       docker-compose restart"
echo "==============================================="
"""
    
    cluster_files["start.sh"] = startup_script
    
    # Generate simple README
    readme_content = f"""# Elasticsearch Development Cluster: {config['cluster_name']}
Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 🛠️ Development Mode
This package contains a simplified development setup with all nodes in a single Docker Compose file.

## Cluster Overview
- **Version**: {config['es_version']}
- **Nodes**: {len(nodes)}
- **Mode**: Development (Single file)

## Node Configuration
"""
    
    for i, node in enumerate(nodes):
        optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
        readme_content += f"""
### {node['name']} (localhost:{node['http_port']})
- **Roles**: {', '.join(node['roles']).title()}
- **Hardware**: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM
- **Heap Size**: {optimal['heap_size']}
"""
    
    readme_content += f"""
## Quick Start

### 1. Start the cluster
```bash
chmod +x start.sh
./start.sh
```

### 2. Alternative: Using Docker Compose directly
```bash
# Create directories
{' && '.join([f'mkdir -p ./{node["name"]}/{{data,logs,backups,config}}' for node in nodes])}

# Start cluster
docker-compose up -d

# Check status
docker-compose ps
```

### 3. Access the cluster
- Primary node: http://localhost:{nodes[0]['http_port']}
- Cluster health: http://localhost:{nodes[0]['http_port']}/_cluster/health
- All nodes: http://localhost:{nodes[0]['http_port']}/_cat/nodes?v

## Management Commands

### Stop the cluster
```bash
docker-compose down
```

### View logs
```bash
docker-compose logs
# Or for specific node:
docker-compose logs {nodes[0]['name']}
```

### Restart the cluster
```bash
docker-compose restart
```

### Clean shutdown with data removal
```bash
docker-compose down -v
```

## Development Benefits
- ✅ **Single file**: Easy to manage and understand
- ✅ **Container networking**: Nodes communicate via Docker network names
- ✅ **No host mapping**: No need for extra hosts configuration
- ✅ **Quick startup**: All containers start together
- ✅ **Easy debugging**: Simple logging and monitoring

## Files Included
- `docker-compose.yml` - Complete cluster definition
- `start.sh` - Simple startup script with health checks
- `README.md` - This documentation

## X-Pack Features
"""
    
    for feature, enabled in config['xpack_settings'].items():
        status = "✅ Enabled" if enabled else "❌ Disabled"
        readme_content += f"- **{feature.title()}**: {status}\n"
    
    readme_content += """
## Troubleshooting

### Container won't start
1. Check Docker is running: `docker info`
2. Check system resources (memory, disk space)
3. Check logs: `docker-compose logs [node_name]`

### Permission errors (Linux/Mac)
```bash
sudo chown -R 1000:1000 ./*/
```

### Memory issues
Ensure vm.max_map_count is set correctly:
```bash
sudo sysctl -w vm.max_map_count=262144
```

## Converting to Production
For production deployment:
1. Generate a new configuration in "Production Mode"
2. Use individual Docker Compose files per node
3. Configure proper networking and host mappings
4. Implement proper backup and monitoring strategies
"""
    
    cluster_files["README.md"] = readme_content
    
    return cluster_files

def generate_cluster_files(config):
    """Generate all files for the cluster organized in folders per node"""
    cluster_files = {}
    nodes = config['nodes']
    
    # Generate README file for root directory
    master_eligible = [n for n in nodes if 'master' in n['roles']]
    min_master_nodes = calculate_minimum_master_nodes(len(nodes), len(master_eligible))
    
    readme_content = f"""# Elasticsearch Cluster: {config['cluster_name']}
Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 📁 Ultra-Streamlined Folder Structure
```
elasticsearch-cluster/
├── README.md                           # This file
├── cluster-init.sh                     # Global system validation
├── start-all.sh                        # Start all nodes sequentially
├── stop-all.sh                         # Stop all nodes
└── nodes/
    ├── {nodes[0]['name']}/                         # Node 1 folder
    │   ├── docker-compose.yml             # All configuration (ES + JVM)
    │   ├── run.sh                          # Start this node (with validation)
    │   └── init.sh                         # Legacy init script
    ├── {nodes[1]['name'] if len(nodes) > 1 else 'els02'}/                         # Node 2 folder
    │   ├── docker-compose.yml
    │   ├── run.sh
    │   └── init.sh
    └── ... (additional nodes)
```

## 🎯 Ultra-Streamlined Configuration Approach

This generator uses an **ultra-streamlined approach** that eliminates ALL redundancy:

✅ **Everything in docker-compose.yml**: All ES settings + JVM options via environment variables
❌ **No separate config files**: No elasticsearch.yml, no JVM options files
❌ **No configuration redundancy**: Single source of truth

### Benefits:
- **Maximum simplicity**: Just 3 files per node (compose + scripts + docs)
- **Zero configuration drift**: Everything in one place
- **Container-native**: Pure Docker/Kubernetes approach
- **Production-ready**: Version-optimized settings built into compose files

## Cluster Overview
- **Version**: {config['es_version']}
- **Nodes**: {len(nodes)}
- **Master Eligible**: {len(master_eligible)}
- **Minimum Master Nodes**: {min_master_nodes}
- **Domain**: {config['primary_domain']}

## Node Configuration Summary
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
- **Location**: `nodes/{node['name']}/`
"""
    
    readme_content += f"""
## 🚀 Quick Start Instructions

### Method 1: Automated Cluster Startup (Recommended)
```bash
# 1. Validate system requirements
chmod +x cluster-init.sh
./cluster-init.sh

# 2. Start all nodes automatically
chmod +x start-all.sh
./start-all.sh
```

### Method 2: Individual Node Management
```bash
# 1. Validate system requirements (run once)
chmod +x cluster-init.sh
./cluster-init.sh

# 2. Start individual nodes
cd nodes/{nodes[0]['name']}
chmod +x run.sh
./run.sh

cd ../els02
./run.sh
# ... repeat for other nodes
```

### Method 3: Manual Docker Compose
```bash
# Navigate to each node directory and start
cd nodes/{nodes[0]['name']}
docker-compose up -d

cd ../els02
docker-compose up -d
# ... repeat for other nodes
```

## 📁 File Organization Benefits

### ✅ Organized Structure
- Each node has its own dedicated folder
- Clear separation of concerns
- Easy to manage individual nodes
- Scalable for large clusters

### ✅ Container Optimizations
- **Version-specific JVM options**: Each node includes `container-optimized.options` with:
  - Elasticsearch 6.x: Legacy GC logging compatible with containers
  - Elasticsearch 7.x: Modern `-Xlog` syntax with stderr output
  - Elasticsearch 8.x: Latest JVM optimizations for containers
- **Container-friendly logging**: All logs output to stderr for better container integration
- **Memory optimization**: Production-grade memory limits and heap sizing

### ✅ Easy Deployment
- Copy individual node folders to respective servers
- Each folder is self-contained
- Simple to version control and maintain

## 🛠️ Container-Optimized Features

### JVM Options (`container-optimized.options`)
Each node includes optimized JVM settings:
- **Heap sizing**: Follows ES best practice (50% of system RAM, max 31GB)
- **GC optimization**: Version-appropriate garbage collection settings
- **Container logging**: All output directed to stderr for container compatibility
- **Error handling**: Heap dumps and error files in appropriate container locations

### Docker Integration
- **Memory limits**: Production-grade container memory limits
- **Health checks**: Built-in container health monitoring
- **Volume mapping**: Proper data, logs, and backup volume mounting
- **Network configuration**: Optimized for both development and production

## Management Commands

### Cluster Operations
```bash
# Check cluster health (from any node)
curl http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cluster/health?pretty

# View all nodes
curl http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cat/nodes?v

# Stop all nodes
./stop-all.sh

# Start all nodes
./start-all.sh
```

### Individual Node Operations
```bash
# Start specific node
cd nodes/<node-name>
./run.sh

# Stop specific node
docker-compose down

# View node logs
docker-compose logs

# Restart node
docker-compose restart
```"""
    
    # Generate global cluster scripts
    cluster_files["README.md"] = readme_content
    
    # Global system validation script
    cluster_files["cluster-init.sh"] = generate_global_init_script(config)
    
    # Global start/stop scripts
    cluster_files["start-all.sh"] = generate_start_all_script(config)
    cluster_files["stop-all.sh"] = generate_stop_all_script(config)
    
    # Generate files for each node in organized folders
    for node in nodes:
        node_folder = f"nodes/{node['name']}"
        
        # Docker Compose file
        compose_file = generate_individual_docker_compose(node, config)
        cluster_files[f"{node_folder}/docker-compose.yml"] = compose_file
        
        # Node-specific run script
        run_script = generate_node_run_script_organized(node, config)
        cluster_files[f"{node_folder}/run.sh"] = run_script
        
        # Legacy init script (for backwards compatibility)
        init_script = generate_init_script_organized(node, config)
        cluster_files[f"{node_folder}/init.sh"] = init_script
        
        # Node-specific README
        node_readme = generate_node_readme(node, config)
        cluster_files[f"{node_folder}/README.md"] = node_readme
    
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
    """Generate system validation script to check requirements without making changes"""
    cluster_name = config['cluster_name']
    
    script_content = f"""#!/bin/bash
# System Requirements Validation Script for Elasticsearch Node: {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Cluster: {cluster_name} | Node: {node['name']} | Roles: {', '.join(node['roles'])}

echo "🔍 System Requirements Validation for Elasticsearch Node: {node['name']}"
echo "📊 Cluster: {cluster_name}"
echo "🎭 Roles: {', '.join(node['roles'])}"
echo "💻 Hardware: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM"
echo "🌐 Network: {node['ip']}:{node['http_port']}"
echo "==============================================="

# Function to check if command exists
command_exists() {{
    command -v "$1" >/dev/null 2>&1
}}

VALIDATION_PASSED=true

# ==================== DOCKER VALIDATION ====================
echo "🐳 Checking Docker installation..."

if ! command_exists docker; then
    echo "❌ Docker not found. Please install Docker."
    VALIDATION_PASSED=false
else
    echo "✅ Docker is installed"
    
    # Check if Docker is running
    if ! docker info > /dev/null 2>&1; then
        echo "❌ Docker is not running. Please start Docker service."
        VALIDATION_PASSED=false
    else
        echo "✅ Docker is running"
        
        # Check Docker version
        DOCKER_VERSION=$(docker --version 2>/dev/null | grep -oP '\\d+\\.\\d+' | head -1)
        echo "ℹ️  Docker version: $DOCKER_VERSION"
    fi
fi

# ==================== DOCKER COMPOSE VALIDATION ====================
echo "🔧 Checking Docker Compose..."

if ! command_exists docker-compose && ! docker compose version > /dev/null 2>&1; then
    echo "❌ Docker Compose not found. Please install Docker Compose."
    VALIDATION_PASSED=false
else
    echo "✅ Docker Compose is available"
    
    if command_exists docker-compose; then
        COMPOSE_VERSION=$(docker-compose --version 2>/dev/null | grep -oP '\\d+\\.\\d+' | head -1)
        echo "ℹ️  Docker Compose version: $COMPOSE_VERSION"
    else
        echo "ℹ️  Using built-in 'docker compose' command"
    fi
fi

# ==================== SYSTEM LIMITS VALIDATION ====================
echo "⚙️ Checking system limits for Elasticsearch..."

# Check vm.max_map_count
current_max_map_count=$(sysctl vm.max_map_count 2>/dev/null | awk '{{print $3}}' || echo "0")
if [[ "$current_max_map_count" -ge 262144 ]] 2>/dev/null; then
    echo "✅ vm.max_map_count: $current_max_map_count (sufficient)"
else
    echo "❌ vm.max_map_count: $current_max_map_count (required: 262144+)"
    echo "   To fix: sudo sysctl -w vm.max_map_count=262144"
    echo "   To persist: echo 'vm.max_map_count=262144' | sudo tee -a /etc/sysctl.conf"
    VALIDATION_PASSED=false
fi

# Check ulimits
echo "📊 Checking file descriptor limits..."
current_nofile=$(ulimit -n)
if [[ "$current_nofile" -ge 65536 ]] 2>/dev/null; then
    echo "✅ File descriptor limit: $current_nofile (sufficient)"
else
    echo "⚠️  File descriptor limit: $current_nofile (recommended: 65536+)"
    echo "   To fix temporarily: ulimit -n 65536"
    echo "   To persist: Add limits to /etc/security/limits.conf"
fi

current_nproc=$(ulimit -u)
echo "ℹ️  Process limit: $current_nproc"

# Check persistent limits configuration
LIMITS_CONF="/etc/security/limits.conf"
if [[ -f "$LIMITS_CONF" ]]; then
    if grep -q "elasticsearch.*nofile.*65536" "$LIMITS_CONF" 2>/dev/null; then
        echo "✅ Persistent file limits configured in $LIMITS_CONF"
    else
        echo "⚠️  No persistent file limits found in $LIMITS_CONF"
        echo "   Consider adding: elasticsearch soft nofile 65536"
        echo "                   elasticsearch hard nofile 65536"
    fi
else
    echo "⚠️  $LIMITS_CONF not found"
fi

# ==================== DIRECTORY VALIDATION ====================
echo "📁 Checking directories for {node['name']}..."

# Required directories
DIRS=(
    "./{node['name']}"
    "./{node['name']}/data"
    "./{node['name']}/logs"
    "./{node['name']}/backups"
    "./{node['name']}/config"
)

missing_dirs=()
for dir in "${{DIRS[@]}}"; do
    if [[ ! -d "$dir" ]]; then
        missing_dirs+=("$dir")
    fi
done

if [[ ${{#missing_dirs[@]}} -eq 0 ]]; then
    echo "✅ All required directories exist"
else
    echo "⚠️  Missing directories: ${{missing_dirs[*]}}"
    echo "   To create: mkdir -p ${{missing_dirs[*]}}"
fi

# ==================== PERMISSIONS VALIDATION ====================
echo "🔐 Checking permissions..."

# Check if directories are writable
writable_issues=()
for dir in "./{node['name']}/data" "./{node['name']}/logs" "./{node['name']}/backups"; do
    if [[ -d "$dir" ]]; then
        if [[ ! -w "$dir" ]]; then
            writable_issues+=("$dir")
        fi
    fi
done

if [[ ${{#writable_issues[@]}} -eq 0 ]]; then
    echo "✅ Directory permissions are correct"
else
    echo "❌ Directories not writable: ${{writable_issues[*]}}"
    echo "   To fix: sudo chown -R 1000:1000 ./{node['name']}/"
    echo "          sudo chmod -R 777 ./{node['name']}/{{data,logs,backups}}"
    VALIDATION_PASSED=false
fi

# ==================== NETWORK VALIDATION ====================
echo "🌐 Checking network requirements..."

# Check if ports are available
if command_exists netstat; then
    HTTP_PORT_USED=$(netstat -tuln 2>/dev/null | grep ":{node['http_port']} " || echo "")
    TRANSPORT_PORT_USED=$(netstat -tuln 2>/dev/null | grep ":{node['transport_port']} " || echo "")
    
    if [[ -z "$HTTP_PORT_USED" ]]; then
        echo "✅ HTTP port {node['http_port']} is available"
    else
        echo "❌ HTTP port {node['http_port']} is already in use"
        VALIDATION_PASSED=false
    fi
    
    if [[ -z "$TRANSPORT_PORT_USED" ]]; then
        echo "✅ Transport port {node['transport_port']} is available"
    else
        echo "❌ Transport port {node['transport_port']} is already in use"
        VALIDATION_PASSED=false
    fi
else
    echo "⚠️  netstat not available - cannot check port availability"
fi

# ==================== MEMORY VALIDATION ====================
echo "💾 Checking memory requirements..."

# Check available memory
if [[ -f /proc/meminfo ]]; then
    TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{{print $2}}')
    TOTAL_MEM_GB=$((TOTAL_MEM_KB / 1024 / 1024))
    
    echo "ℹ️  Total system memory: ${{TOTAL_MEM_GB}}GB"
    
    if [[ $TOTAL_MEM_GB -ge {node['ram_gb']} ]]; then
        echo "✅ Sufficient memory for {node['ram_gb']}GB allocation"
    else
        echo "❌ Insufficient memory: ${{TOTAL_MEM_GB}}GB available, {node['ram_gb']}GB required"
        VALIDATION_PASSED=false
    fi
else
    echo "⚠️  Cannot determine system memory"
fi

# ==================== DISK SPACE VALIDATION ====================
echo "💿 Checking disk space..."

if command_exists df; then
    CURRENT_DIR_SPACE=$(df . | tail -1 | awk '{{print $4}}')
    CURRENT_DIR_SPACE_GB=$((CURRENT_DIR_SPACE / 1024 / 1024))
    
    echo "ℹ️  Available disk space: ${{CURRENT_DIR_SPACE_GB}}GB"
    
    # Estimate minimum space needed (conservative estimate)
    MIN_SPACE_GB=10
    if [[ $CURRENT_DIR_SPACE_GB -ge $MIN_SPACE_GB ]]; then
        echo "✅ Sufficient disk space"
    else
        echo "❌ Low disk space: ${{CURRENT_DIR_SPACE_GB}}GB available, ${{MIN_SPACE_GB}}GB minimum recommended"
        VALIDATION_PASSED=false
    fi
else
    echo "⚠️  Cannot check disk space"
fi

# ==================== FINAL VALIDATION REPORT ====================
echo ""
echo "==============================================="
echo "🔍 VALIDATION SUMMARY"
echo "==============================================="

if [[ "$VALIDATION_PASSED" == "true" ]]; then
    echo "✅ All system requirements validated successfully!"
    echo ""
    echo "📋 System is ready for Elasticsearch deployment:"
    echo "   ✅ Docker installed and running"
    echo "   ✅ System limits properly configured"
    echo "   ✅ Directories and permissions ready"
    echo "   ✅ Network ports available"
    echo "   ✅ Sufficient resources"
    echo ""
    echo "🚀 Next steps:"
    echo "   1. Run: ./run-{node['name']}.sh"
    echo "   2. Monitor: curl http://{node['ip']}:{node['http_port']}/_cluster/health"
else
    echo "❌ System requirements validation FAILED!"
    echo ""
    echo "📋 Issues found that need to be resolved:"
    echo "   Please address the ❌ items listed above"
    echo ""
    echo "💡 Common fixes:"
    echo "   • Install Docker: https://docs.docker.com/get-docker/"
    echo "   • Set vm.max_map_count: sudo sysctl -w vm.max_map_count=262144"
    echo "   • Create directories: mkdir -p ./{node['name']}/{{data,logs,backups,config}}"
    echo "   • Fix permissions: sudo chown -R 1000:1000 ./{node['name']}/"
fi

echo "==============================================="
exit $([ "$VALIDATION_PASSED" = "true" ] && echo 0 || echo 1)
"""
    
    return script_content

def generate_node_run_script(node, config):
    """Generate node-specific run script for starting Elasticsearch with validation-only checks"""
    cluster_name = config['cluster_name']
    
    script_content = f"""#!/bin/bash
# Run Script for Elasticsearch Node: {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Cluster: {cluster_name} | Node: {node['name']} | Roles: {', '.join(node['roles'])}

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

VALIDATION_PASSED=true
VALIDATION_WARNINGS=()

# ==================== PRE-FLIGHT CHECKS ====================
echo "🔍 Running pre-flight validation checks..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    VALIDATION_PASSED=false
else
    echo "✅ Docker is running"
fi

# Check if Docker Compose is available
if ! command_exists docker-compose && ! docker compose version > /dev/null 2>&1; then
    echo "❌ Docker Compose not found. Please install Docker Compose."
    VALIDATION_PASSED=false
else
    echo "✅ Docker Compose is available"
fi

# Check if compose file exists
COMPOSE_FILE="docker-compose-{node['name']}.yml"
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "❌ Docker Compose file not found: $COMPOSE_FILE"
    echo "   Please ensure you've extracted all files from the cluster package."
    VALIDATION_PASSED=false
else
    echo "✅ Docker Compose file found: $COMPOSE_FILE"
fi

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

missing_dirs=()
for dir in "${{DIRS[@]}}"; do
    if [[ ! -d "$dir" ]]; then
        missing_dirs+=("$dir")
    fi
done

if [[ ${{#missing_dirs[@]}} -eq 0 ]]; then
    echo "✅ All required directories exist"
else
    echo "❌ Missing directories: ${{missing_dirs[*]}}"
    echo "   To create: mkdir -p ${{missing_dirs[*]}}"
    VALIDATION_PASSED=false
fi

# ==================== PERMISSIONS VALIDATION ====================
echo "🔐 Checking permissions..."

# Check if directories are writable (only if they exist)
writable_issues=()
for dir in "./{node['name']}/data" "./{node['name']}/logs" "./{node['name']}/backups"; do
    if [[ -d "$dir" ]]; then
        if [[ ! -w "$dir" ]]; then
            writable_issues+=("$dir")
        fi
    fi
done

if [[ ${{#writable_issues[@]}} -eq 0 ]]; then
    echo "✅ Directory permissions are correct"
else
    echo "❌ Directories not writable: ${{writable_issues[*]}}"
    echo "   To fix: sudo chown -R 1000:1000 ./{node['name']}/"
    echo "          sudo chmod -R 777 ./{node['name']}/{{data,logs,backups}}"
    VALIDATION_PASSED=false
fi

# ==================== SYSTEM LIMITS VALIDATION ====================
echo "⚙️ Checking system limits..."

# Check vm.max_map_count
current_max_map_count=$(sysctl vm.max_map_count 2>/dev/null | awk '{{print $3}}' || echo "0")
if [[ "$current_max_map_count" -ge 262144 ]] 2>/dev/null; then
    echo "✅ vm.max_map_count: $current_max_map_count (sufficient)"
else
    echo "❌ vm.max_map_count: $current_max_map_count (required: 262144+)"
    echo "   To fix: sudo sysctl -w vm.max_map_count=262144"
    VALIDATION_PASSED=false
fi

# Check ulimits
current_nofile=$(ulimit -n)
if [[ "$current_nofile" -ge 65536 ]] 2>/dev/null; then
    echo "✅ File descriptor limit: $current_nofile (sufficient)"
else
    echo "⚠️  File descriptor limit: $current_nofile (recommended: 65536+)"
    echo "   To fix temporarily: ulimit -n 65536"
    VALIDATION_WARNINGS+=("File descriptor limit is low")
fi

# ==================== PORT AVAILABILITY CHECK ====================
echo "🌐 Checking port availability..."

if command_exists netstat; then
    HTTP_PORT_USED=$(netstat -tuln 2>/dev/null | grep ":{node['http_port']} " || echo "")
    TRANSPORT_PORT_USED=$(netstat -tuln 2>/dev/null | grep ":{node['transport_port']} " || echo "")
    
    if [[ -z "$HTTP_PORT_USED" ]]; then
        echo "✅ HTTP port {node['http_port']} is available"
    else
        echo "❌ HTTP port {node['http_port']} is already in use"
        VALIDATION_PASSED=false
    fi
    
    if [[ -z "$TRANSPORT_PORT_USED" ]]; then
        echo "✅ Transport port {node['transport_port']} is available"
    else
        echo "❌ Transport port {node['transport_port']} is already in use"
        VALIDATION_PASSED=false
    fi
else
    echo "⚠️  netstat not available - cannot check port availability"
    VALIDATION_WARNINGS+=("Cannot verify port availability")
fi

# ==================== VALIDATION SUMMARY ====================
echo ""
echo "==============================================="
echo "🔍 PRE-FLIGHT VALIDATION SUMMARY"
echo "==============================================="

if [[ "$VALIDATION_PASSED" == "false" ]]; then
    echo "❌ VALIDATION FAILED - Cannot proceed with container startup"
    echo ""
    echo "📋 Critical issues found (marked with ❌ above):"
    echo "   Please resolve all ❌ issues before running this script again"
    echo ""
    echo "💡 Common fixes:"
    echo "   • Start Docker: sudo systemctl start docker"
    echo "   • Install Docker Compose: https://docs.docker.com/compose/install/"
    echo "   • Set vm.max_map_count: sudo sysctl -w vm.max_map_count=262144"
    echo "   • Create directories: mkdir -p ./{node['name']}/{{data,logs,backups,config}}"
    echo "   • Fix permissions: sudo chown -R 1000:1000 ./{node['name']}/"
    echo ""
    echo "💡 Run the validation script for detailed guidance:"
    echo "   ./init.sh"
    echo "==============================================="
    exit 1
fi

# Show warnings if any
if [[ ${{#VALIDATION_WARNINGS[@]}} -gt 0 ]]; then
    echo "⚠️  VALIDATION WARNINGS:"
    for warning in "${{VALIDATION_WARNINGS[@]}}"; do
        echo "   • $warning"
    done
    echo ""
    echo "ℹ️  These warnings won't prevent startup but may affect performance"
    echo ""
fi

echo "✅ All critical validations passed - proceeding with container startup"
echo ""

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
        echo "   3. Run validation script: ./init.sh"
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

def generate_global_init_script(config):
    """Generate global system validation script for the entire cluster"""
    cluster_name = config['cluster_name']
    nodes = config['nodes']
    
    script_content = f"""#!/bin/bash
# Global System Validation Script for Elasticsearch Cluster: {cluster_name}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Total Nodes: {len(nodes)}

echo "🔍 Global System Validation for Elasticsearch Cluster: {cluster_name}"
echo "📊 Total Nodes: {len(nodes)}"
echo "🎭 Node Types: {', '.join(set([', '.join(n['roles']) for n in nodes]))}"
echo "==============================================="

VALIDATION_PASSED=true

# Function to check if command exists
command_exists() {{
    command -v "$1" >/dev/null 2>&1
}}

# ==================== DOCKER VALIDATION ====================
echo "🐳 Checking Docker installation..."

if ! command_exists docker; then
    echo "❌ Docker not found. Please install Docker."
    VALIDATION_PASSED=false
else
    echo "✅ Docker is installed"
    
    # Check if Docker is running
    if ! docker info > /dev/null 2>&1; then
        echo "❌ Docker is not running. Please start Docker service."
        VALIDATION_PASSED=false
    else
        echo "✅ Docker is running"
        DOCKER_VERSION=$(docker --version 2>/dev/null | grep -oP '\\d+\\.\\d+' | head -1)
        echo "ℹ️  Docker version: $DOCKER_VERSION"
    fi
fi

# ==================== DOCKER COMPOSE VALIDATION ====================
echo "🔧 Checking Docker Compose..."

if ! command_exists docker-compose && ! docker compose version > /dev/null 2>&1; then
    echo "❌ Docker Compose not found. Please install Docker Compose."
    VALIDATION_PASSED=false
else
    echo "✅ Docker Compose is available"
fi

# ==================== SYSTEM LIMITS VALIDATION ====================
echo "⚙️ Checking system limits for Elasticsearch..."

# Check vm.max_map_count
current_max_map_count=$(sysctl vm.max_map_count 2>/dev/null | awk '{{print $3}}' || echo "0")
if [[ "$current_max_map_count" -ge 262144 ]] 2>/dev/null; then
    echo "✅ vm.max_map_count: $current_max_map_count (sufficient)"
else
    echo "❌ vm.max_map_count: $current_max_map_count (required: 262144+)"
    echo "   To fix: sudo sysctl -w vm.max_map_count=262144"
    VALIDATION_PASSED=false
fi

# ==================== NODE DIRECTORY VALIDATION ====================
echo "📁 Checking node directories..."

nodes_dir="nodes"
if [[ ! -d "$nodes_dir" ]]; then
    echo "❌ Nodes directory not found: $nodes_dir"
    VALIDATION_PASSED=false
else
    echo "✅ Nodes directory exists: $nodes_dir"
    
    # Check each node directory
    missing_nodes=()"""

    for node in nodes:
        script_content += f"""
    if [[ ! -d "$nodes_dir/{node['name']}" ]]; then
        missing_nodes+=("{node['name']}")
    fi"""

    script_content += f"""
    
    if [[ ${{#missing_nodes[@]}} -eq 0 ]]; then
        echo "✅ All node directories exist"
    else
        echo "❌ Missing node directories: ${{missing_nodes[*]}}"
        VALIDATION_PASSED=false
    fi
fi

# ==================== PORT AVAILABILITY CHECK ====================
echo "🌐 Checking port availability for all nodes..."

if command_exists netstat; then
    port_conflicts=()"""

    for node in nodes:
        script_content += f"""
    if netstat -tuln 2>/dev/null | grep ":{node['http_port']} " > /dev/null; then
        port_conflicts+=("{node['name']}:HTTP:{node['http_port']}")
    fi
    if netstat -tuln 2>/dev/null | grep ":{node['transport_port']} " > /dev/null; then
        port_conflicts+=("{node['name']}:Transport:{node['transport_port']}")
    fi"""

    script_content += f"""
    
    if [[ ${{#port_conflicts[@]}} -eq 0 ]]; then
        echo "✅ All ports are available"
    else
        echo "❌ Port conflicts detected:"
        for conflict in "${{port_conflicts[@]}}"; do
            echo "   • $conflict"
        done
        VALIDATION_PASSED=false
    fi
else
    echo "⚠️  netstat not available - cannot check port availability"
fi

# ==================== FINAL VALIDATION REPORT ====================
echo ""
echo "==============================================="
echo "🔍 GLOBAL VALIDATION SUMMARY"
echo "==============================================="

if [[ "$VALIDATION_PASSED" == "true" ]]; then
    echo "✅ All system requirements validated successfully!"
    echo ""
    echo "📋 System is ready for Elasticsearch cluster deployment:"
    echo "   ✅ Docker installed and running"
    echo "   ✅ System limits properly configured"
    echo "   ✅ All node directories exist"
    echo "   ✅ Network ports available"
    echo ""
    echo "🚀 Next steps:"
    echo "   1. Run: ./start-all.sh (automatic cluster startup)"
    echo "   2. Or manually: cd nodes/<node-name> && ./run.sh"
    echo "   3. Monitor: curl http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cluster/health"
else
    echo "❌ System requirements validation FAILED!"
    echo ""
    echo "📋 Issues found that need to be resolved:"
    echo "   Please address the ❌ items listed above"
    echo ""
    echo "💡 Common fixes:"
    echo "   • Install Docker: https://docs.docker.com/get-docker/"
    echo "   • Set vm.max_map_count: sudo sysctl -w vm.max_map_count=262144"
    echo "   • Check file structure and node directories"
fi

echo "==============================================="
exit $([ "$VALIDATION_PASSED" = "true" ] && echo 0 || echo 1)
"""
    
    return script_content

def generate_start_all_script(config):
    """Generate script to start all nodes sequentially"""
    cluster_name = config['cluster_name']
    nodes = config['nodes']
    
    script_content = f"""#!/bin/bash
# Start All Nodes Script for Elasticsearch Cluster: {cluster_name}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

echo "🚀 Starting Elasticsearch Cluster: {cluster_name}"
echo "📊 Total Nodes: {len(nodes)}"
echo "==============================================="

# Function to check if command exists
command_exists() {{
    command -v "$1" >/dev/null 2>&1
}}

# Determine docker-compose command
if command_exists docker-compose; then
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi

FAILED_NODES=()
STARTED_NODES=()

# Start nodes sequentially"""

    for i, node in enumerate(nodes):
        script_content += f"""

echo ""
echo "🖥️ Starting Node {i+1}/{len(nodes)}: {node['name']}"
echo "   Roles: {', '.join(node['roles']).title()}"
echo "   Location: nodes/{node['name']}"

if [[ -d "nodes/{node['name']}" ]]; then
    cd "nodes/{node['name']}"
    
    if [[ -f "docker-compose.yml" ]]; then
        echo "   📄 Found docker-compose.yml"
        
        # Start the container
        if $COMPOSE_CMD up -d > /dev/null 2>&1; then
            echo "   ✅ Container started successfully"
            
            # Wait for health check
            echo "   ⏳ Waiting for {node['name']} to be ready..."
            for j in {{1..30}}; do
                if curl -f http://{node['ip']}:{node['http_port']}/_cluster/health > /dev/null 2>&1; then
                    echo "   ✅ {node['name']} is healthy and ready!"
                    STARTED_NODES+=("{node['name']}")
                    break
                fi
                
                if [[ $j -eq 30 ]]; then
                    echo "   ⚠️  {node['name']} started but not responding to health checks"
                    STARTED_NODES+=("{node['name']} (unverified)")
                    break
                fi
                
                sleep 2
            done
        else
            echo "   ❌ Failed to start {node['name']}"
            FAILED_NODES+=("{node['name']}")
        fi
    else
        echo "   ❌ docker-compose.yml not found in nodes/{node['name']}"
        FAILED_NODES+=("{node['name']} (no compose file)")
    fi
    
    cd "../.."
else
    echo "   ❌ Directory not found: nodes/{node['name']}"
    FAILED_NODES+=("{node['name']} (no directory)")
fi"""

    script_content += f"""

echo ""
echo "==============================================="
echo "📊 CLUSTER STARTUP SUMMARY"
echo "==============================================="

if [[ ${{#FAILED_NODES[@]}} -eq 0 ]]; then
    echo "✅ All nodes started successfully!"
    echo ""
    echo "🖥️ Started Nodes (${{#STARTED_NODES[@]}}):"
    for node in "${{STARTED_NODES[@]}}"; do
        echo "   ✅ $node"
    done
    
    echo ""
    echo "🏥 Cluster Health Check:"
    echo "⏳ Waiting for cluster to stabilize..."
    sleep 10
    
    if curl -s http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cluster/health?pretty 2>/dev/null; then
        echo ""
        echo "🖥️ Cluster Nodes:"
        curl -s http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cat/nodes?v 2>/dev/null || echo "❌ Could not retrieve node list"
    else
        echo "❌ Could not retrieve cluster health"
    fi
    
else
    echo "⚠️ Some nodes failed to start:"
    echo ""
    echo "✅ Started Nodes (${{#STARTED_NODES[@]}}):"
    for node in "${{STARTED_NODES[@]}}"; do
        echo "   ✅ $node"
    done
    
    echo ""
    echo "❌ Failed Nodes (${{#FAILED_NODES[@]}}):"
    for node in "${{FAILED_NODES[@]}}"; do
        echo "   ❌ $node"
    done
    
    echo ""
    echo "💡 Troubleshooting:"
    echo "   • Check logs: cd nodes/<node-name> && docker-compose logs"
    echo "   • Manual start: cd nodes/<node-name> && ./run.sh"
    echo "   • System validation: ./cluster-init.sh"
fi

echo ""
echo "🔗 Access URLs:"
"""

    for node in nodes:
        script_content += f"""echo "   {node['name']}: http://{node['ip']}:{node['http_port']}" """

    script_content += f"""

echo ""
echo "📋 Management Commands:"
echo "   Stop cluster:     ./stop-all.sh"
echo "   Check health:     curl http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cluster/health?pretty"
echo "   View nodes:       curl http://{nodes[0]['ip']}:{nodes[0]['http_port']}/_cat/nodes?v"
echo "==============================================="
"""
    
    return script_content

def generate_stop_all_script(config):
    """Generate script to stop all nodes"""
    cluster_name = config['cluster_name']
    nodes = config['nodes']
    
    script_content = f"""#!/bin/bash
# Stop All Nodes Script for Elasticsearch Cluster: {cluster_name}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

echo "🛑 Stopping Elasticsearch Cluster: {cluster_name}"
echo "📊 Total Nodes: {len(nodes)}"
echo "==============================================="

# Function to check if command exists
command_exists() {{
    command -v "$1" >/dev/null 2>&1
}}

# Determine docker-compose command
if command_exists docker-compose; then
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi

STOPPED_NODES=()
FAILED_STOPS=()

# Stop nodes in reverse order (good practice for ES clusters)"""

    for i, node in enumerate(reversed(nodes)):
        script_content += f"""

echo ""
echo "🛑 Stopping Node {i+1}/{len(nodes)}: {node['name']}"

if [[ -d "nodes/{node['name']}" ]]; then
    cd "nodes/{node['name']}"
    
    if [[ -f "docker-compose.yml" ]]; then
        if $COMPOSE_CMD down > /dev/null 2>&1; then
            echo "   ✅ {node['name']} stopped successfully"
            STOPPED_NODES+=("{node['name']}")
        else
            echo "   ❌ Failed to stop {node['name']}"
            FAILED_STOPS+=("{node['name']}")
        fi
    else
        echo "   ⚠️  docker-compose.yml not found"
        FAILED_STOPS+=("{node['name']} (no compose file)")
    fi
    
    cd "../.."
else
    echo "   ⚠️  Directory not found: nodes/{node['name']}"
    FAILED_STOPS+=("{node['name']} (no directory)")
fi"""

    script_content += f"""

echo ""
echo "==============================================="
echo "📊 CLUSTER SHUTDOWN SUMMARY"
echo "==============================================="

if [[ ${{#FAILED_STOPS[@]}} -eq 0 ]]; then
    echo "✅ All nodes stopped successfully!"
    echo ""
    echo "🛑 Stopped Nodes (${{#STOPPED_NODES[@]}}):"
    for node in "${{STOPPED_NODES[@]}}"; do
        echo "   ✅ $node"
    done
else
    echo "⚠️ Some issues during shutdown:"
    echo ""
    echo "✅ Stopped Nodes (${{#STOPPED_NODES[@]}}):"
    for node in "${{STOPPED_NODES[@]}}"; do
        echo "   ✅ $node"
    done
    
    echo ""
    echo "❌ Failed to Stop (${{#FAILED_STOPS[@]}}):"
    for node in "${{FAILED_STOPS[@]}}"; do
        echo "   ❌ $node"
    done
    
    echo ""
    echo "💡 Manual cleanup:"
    echo "   • Check running containers: docker ps"
    echo "   • Force stop: docker stop <container-name>"
    echo "   • Remove: docker rm <container-name>"
fi

echo ""
echo "📋 Cluster Management:"
echo "   Start cluster:    ./start-all.sh"
echo "   Validate system:  ./cluster-init.sh"
echo "==============================================="
"""
    
    return script_content

def generate_node_run_script_organized(node, config):
    """Generate node-specific run script for organized folder structure"""
    cluster_name = config['cluster_name']
    
    script_content = f"""#!/bin/bash
# Run Script for Elasticsearch Node: {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Location: nodes/{node['name']}/run.sh

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

VALIDATION_PASSED=true

# ==================== PRE-FLIGHT CHECKS ====================
echo "🔍 Running pre-flight validation checks..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    VALIDATION_PASSED=false
else
    echo "✅ Docker is running"
fi

# Check if Docker Compose is available
if ! command_exists docker-compose && ! docker compose version > /dev/null 2>&1; then
    echo "❌ Docker Compose not found. Please install Docker Compose."
    VALIDATION_PASSED=false
else
    echo "✅ Docker Compose is available"
fi

# Check if compose file exists
COMPOSE_FILE="docker-compose.yml"
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "❌ Docker Compose file not found: $COMPOSE_FILE"
    echo "   Please ensure you're in the correct node directory"
    VALIDATION_PASSED=false
else
    echo "✅ Docker Compose file found: $COMPOSE_FILE"
fi

# ==================== DIRECTORY VALIDATION ====================
echo "📁 Validating directories for {node['name']}..."

# Required directories (relative to node folder)
DIRS=(
    "."
    "data"
    "logs" 
    "backups"
    "config"
)

missing_dirs=()
for dir in "${{DIRS[@]}}"; do
    if [[ ! -d "$dir" ]]; then
        missing_dirs+=("$dir")
    fi
done

if [[ ${{#missing_dirs[@]}} -eq 0 ]]; then
    echo "✅ All required directories exist"
else
    echo "❌ Missing directories: ${{missing_dirs[*]}}"
    echo "   Creating missing directories..."
    for dir in "${{missing_dirs[@]}}"; do
        mkdir -p "$dir"
        echo "   📁 Created: $dir"
    done
fi

# ==================== PERMISSIONS VALIDATION ====================
echo "🔐 Checking permissions..."

# Check if directories are writable
writable_issues=()
for dir in "data" "logs" "backups"; do
    if [[ -d "$dir" ]]; then
        if [[ ! -w "$dir" ]]; then
            writable_issues+=("$dir")
        fi
    fi
done

if [[ ${{#writable_issues[@]}} -eq 0 ]]; then
    echo "✅ Directory permissions are correct"
else
    echo "❌ Directories not writable: ${{writable_issues[*]}}"
    echo "   Attempting to fix permissions..."
    for dir in "${{writable_issues[@]}}"; do
        chmod 755 "$dir" 2>/dev/null && echo "   ✅ Fixed: $dir" || echo "   ❌ Could not fix: $dir"
    done
fi

# ==================== CONTAINER MANAGEMENT ====================
if [[ "$VALIDATION_PASSED" == "false" ]]; then
    echo ""
    echo "❌ PRE-FLIGHT VALIDATION FAILED"
    echo "   Please resolve the issues above before starting the container"
    exit 1
fi

echo ""
echo "✅ Pre-flight validation passed - proceeding with container startup"
echo ""

# Determine docker-compose command
if command_exists docker-compose; then
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi

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
        
        exit 0
    else
        echo "🔄 Starting existing container..."
        docker start {node['name']}
    fi
else
    echo "🚀 Creating and starting new container..."
    $COMPOSE_CMD up -d
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
        echo "   3. Run global validation: ../../cluster-init.sh"
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

# Elasticsearch node info
echo ""
echo "🖥️ Node Information:"
curl -s http://{node['ip']}:{node['http_port']}/_nodes/{node['name']}?pretty 2>/dev/null | grep -E '"name"|"version"|"roles"' || echo "❌ Could not retrieve node info"

echo ""
echo "==============================================="
echo "✅ {node['name']} is running successfully!"
echo ""
echo "🔗 Node URLs:"
echo "   Health: http://{node['ip']}:{node['http_port']}/_cluster/health"
echo "   Stats:  http://{node['ip']}:{node['http_port']}/_nodes/stats"
echo ""
echo "📋 Management Commands:"
echo "   View logs:    docker logs {node['name']}"
echo "   Follow logs:  docker logs -f {node['name']}"
echo "   Stop node:    docker-compose down"
echo "   Restart:      docker-compose restart"
echo "==============================================="
"""
    
    return script_content

def generate_init_script_organized(node, config):
    """Generate legacy init script for organized folder structure"""
    cluster_name = config['cluster_name']
    
    script_content = f"""#!/bin/bash
# Legacy Initialization Script for {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Location: nodes/{node['name']}/init.sh
# Note: This is for backwards compatibility. Use run.sh for better functionality.

echo "🔧 Legacy Init Script for {node['name']}"
echo "📊 Cluster: {cluster_name}"
echo "⚠️  Note: Consider using run.sh for enhanced functionality"
echo "==============================================="

# Create directories
echo "📁 Creating directories..."
mkdir -p {{data,logs,backups,config}}

# Set basic permissions
echo "🔐 Setting basic permissions..."
chmod 755 {{data,logs,backups,config}}

# Create basic elasticsearch.yml if it doesn't exist
if [[ ! -f "config/elasticsearch.yml" ]]; then
    echo "📝 Creating basic elasticsearch.yml..."
    cat > config/elasticsearch.yml << 'EOF'
# Basic Elasticsearch configuration for {node['name']}
cluster.name: {cluster_name}
node.name: {node['name']}
network.host: 0.0.0.0
http.port: 9200
transport.tcp.port: 9300

# Node roles
node.master: {'true' if 'master' in node['roles'] else 'false'}
node.data: {'true' if 'data' in node['roles'] else 'false'}
node.ingest: {'true' if 'ingest' in node['roles'] else 'false'}

# Memory lock
bootstrap.memory_lock: true
EOF
else
    echo "✅ elasticsearch.yml already exists"
fi

echo ""
echo "✅ Legacy initialization complete!"
echo ""
echo "🚀 Next steps:"
echo "   1. Run: ./run.sh (recommended)"
echo "   2. Or: docker-compose up -d (manual)"
echo "==============================================="
"""
    
    return script_content

def generate_elasticsearch_yml(node, config):
    """Generate basic elasticsearch.yml configuration file"""
    cluster_name = config['cluster_name']
    nodes = config['nodes']
    
    # Get version-specific configuration
    version_config = get_version_specific_settings(config['es_version'], node, nodes, cluster_name, config)
    
    yml_content = f"""# Elasticsearch Configuration for {node['name']}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Version: {config['es_version']}
# Location: nodes/{node['name']}/config/elasticsearch.yml

# ==================== CLUSTER CONFIGURATION ====================
cluster.name: {cluster_name}
node.name: {node['name']}

# ==================== NODE ROLES ===================="""

    # Add role configuration based on ES version
    if version_config['version_major'] >= 8:
        roles_array = '[' + ','.join([f'"{role}"' for role in node['roles']]) + ']'
        yml_content += f"""
node.roles: {roles_array}"""
    else:
        yml_content += f"""
node.master: {'true' if 'master' in node['roles'] else 'false'}
node.data: {'true' if 'data' in node['roles'] else 'false'}
node.ingest: {'true' if 'ingest' in node['roles'] else 'false'}"""

    yml_content += f"""

# ==================== NETWORK CONFIGURATION ====================
network.host: 0.0.0.0
network.publish_host: {node['ip']}
http.port: 9200
transport.tcp.port: 9300

# ==================== DISCOVERY CONFIGURATION ===================="""

    # Add discovery configuration based on version
    if version_config['version_major'] == 6:
        discovery_hosts = ','.join([n['hostname'] for n in nodes])
        master_eligible = [n for n in nodes if 'master' in n['roles']]
        min_master_nodes = calculate_minimum_master_nodes(len(nodes), len(master_eligible))
        yml_content += f"""
discovery.zen.ping.unicast.hosts: [{discovery_hosts}]
discovery.zen.minimum_master_nodes: {min_master_nodes}
discovery.zen.fd.ping_timeout: 30s"""
    else:
        discovery_hosts = ','.join([f'"{n["hostname"]}:9300"' for n in nodes])
        master_eligible = [n for n in nodes if 'master' in n['roles']]
        master_nodes_list = ','.join([f'"{n["name"]}"' for n in master_eligible])
        yml_content += f"""
discovery.seed_hosts: [{discovery_hosts}]
cluster.initial_master_nodes: [{master_nodes_list}]"""

    yml_content += f"""

# ==================== MEMORY CONFIGURATION ====================
bootstrap.memory_lock: true

# ==================== PATH CONFIGURATION ====================
path.data: /usr/share/elasticsearch/data
path.logs: /usr/share/elasticsearch/logs

# ==================== BASIC PERFORMANCE SETTINGS ===================="""

    optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
    yml_content += f"""
indices.memory.index_buffer_size: {optimal['memory_settings']['index_buffer_size']}
indices.queries.cache.size: {optimal['cache_settings']['queries_cache_size']}

# ==================== X-PACK SETTINGS ===================="""

    # Add X-Pack settings based on version
    xpack_config = config['xpack_settings']
    if version_config['version_major'] >= 8:
        if not xpack_config.get('security', False):
            yml_content += """
xpack.security.enabled: false
xpack.security.enrollment.enabled: false"""
        else:
            yml_content += """
xpack.security.enabled: true
xpack.security.authc.api_key.enabled: true"""
    else:
        yml_content += f"""
xpack.security.enabled: {'true' if xpack_config.get('security', False) else 'false'}"""

    for setting, enabled in xpack_config.items():
        if setting != 'security':
            if setting == 'ilm' and version_config['version_major'] == 6:
                continue  # ILM not available in v6
            yml_content += f"""
xpack.{setting}.enabled: {'true' if enabled else 'false'}"""

    yml_content += f"""

# ==================== DEVELOPMENT SETTINGS ====================
# Note: These settings are for development/testing
# For production, review and adjust security, networking, and performance settings
http.cors.enabled: true
http.cors.allow-origin: "*"
"""

    return yml_content

def generate_node_readme(node, config):
    """Generate README for individual node"""
    cluster_name = config['cluster_name']
    optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
    
    readme_content = f"""# Elasticsearch Node: {node['name']}

## Node Information
- **Cluster**: {cluster_name}
- **Version**: {config['es_version']}
- **Roles**: {', '.join(node['roles']).title()}
- **Hardware**: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM
- **Network**: {node['ip']}:{node['http_port']}
- **Heap Size**: {optimal['heap_size']}

## 📁 Files in this Directory (Ultra-Streamlined)
- `docker-compose.yml` - **Everything**: All ES + JVM settings via environment variables
- `run.sh` - Start this node with pre-flight validation and health checks
- `init.sh` - Legacy initialization script (backwards compatibility)
- `README.md` - This file

**Note**: Zero redundancy! Everything (Elasticsearch + JVM settings) is in `docker-compose.yml` - no separate config files at all!

## 🚀 Quick Start

### Start this node
```bash
chmod +x run.sh
./run.sh
```

### Alternative: Manual Docker Compose
```bash
docker-compose up -d
```

### Check node status
```bash
curl http://{node['ip']}:{node['http_port']}/_cluster/health?pretty
```

## 🔧 Configuration Details

### Memory Configuration
- **System RAM**: {node['ram_gb']}GB
- **Elasticsearch Heap**: {optimal['heap_size']}
- **Container Limit**: {optimal['production_memory_limits']['container_limit_gb']}GB
- **Off-Heap Memory**: {optimal['production_memory_limits']['off_heap_gb']}GB

### Performance Settings
- **Search Threads**: {optimal['thread_pools']['search']}
- **Index Threads**: {optimal['thread_pools']['index']}
- **Bulk Threads**: {optimal['thread_pools']['bulk']}
- **GC Collector**: {optimal['jvm_settings']['gc_collector']}

### Capacity Estimates
- **Data Capacity**: ~{optimal['capacity_estimates']['data_capacity_gb']:,}GB
- **Concurrent Searches**: ~{optimal['capacity_estimates']['concurrent_searches']:,}
- **Indexing Rate**: ~{optimal['capacity_estimates']['indexing_rate_docs_per_sec']:,} docs/sec

## 📊 Management Commands

### Container Management
```bash
# Start
./run.sh

# Stop
docker-compose down

# Restart
docker-compose restart

# View logs
docker-compose logs

# Follow logs
docker-compose logs -f
```

### Health Monitoring
```bash
# Node health
curl http://{node['ip']}:{node['http_port']}/_cluster/health?pretty

# Node stats
curl http://{node['ip']}:{node['http_port']}/_nodes/stats?pretty

# Node info
curl http://{node['ip']}:{node['http_port']}/_nodes/{node['name']}?pretty
```

## 🐳 Container Details

### Volume Mounts
- `./data` → `/usr/share/elasticsearch/data`
- `./logs` → `/usr/share/elasticsearch/logs`
- `./backups` → `/usr/share/elasticsearch/backups`

### Network Ports
- **HTTP**: {node['http_port']} → 9200
- **Transport**: {node['transport_port']} → 9300

### Resource Limits
- **Memory**: {optimal['production_memory_limits']['container_limit_gb']}GB
- **CPU**: {node['cpu_cores']} cores

## 🔧 Troubleshooting

### Common Issues
1. **Container won't start**
   - Check Docker is running: `docker info`
   - Check system requirements: `../../cluster-init.sh`
   - Check logs: `docker-compose logs`

2. **Permission errors**
   - Fix permissions: `chmod 755 data logs backups`
   - Set ownership: `sudo chown -R 1000:1000 .`

3. **Memory issues**
   - Check vm.max_map_count: `sysctl vm.max_map_count`
   - Should be ≥ 262144: `sudo sysctl -w vm.max_map_count=262144`

4. **Network issues**
   - Check port availability: `netstat -tuln | grep {node['http_port']}`
   - Verify IP configuration and extra hosts

### Log Locations
- **Container logs**: `docker-compose logs`
- **Elasticsearch logs**: `./logs/` directory
- **JVM errors**: `./logs/hs_err_pid*.log`

## 🔗 Related Files
- **Global validation**: `../../cluster-init.sh`
- **Start all nodes**: `../../start-all.sh`
- **Stop all nodes**: `../../stop-all.sh`
- **Cluster README**: `../../README.md`
"""
    
    return readme_content

def generate_cluster_visualization(config):
    """Generate dynamic Mermaid diagram based on cluster configuration"""
    nodes = config['nodes']
    cluster_name = config['cluster_name']
    
    if not nodes:
        return "graph TD\n    A[No nodes configured]"
    
    # Categorize nodes by their roles
    master_only_nodes = [n for n in nodes if n['roles'] == ['master']]
    master_data_nodes = [n for n in nodes if 'master' in n['roles'] and 'data' in n['roles']]
    data_only_nodes = [n for n in nodes if 'data' in n['roles'] and 'master' not in n['roles']]
    all_master_eligible = master_only_nodes + master_data_nodes
    all_data_nodes = master_data_nodes + data_only_nodes
    
    # Build Mermaid diagram
    mermaid_code = "graph TD\n"
    
    # Load balancer (if multiple master-eligible nodes)
    if len(all_master_eligible) > 1:
        mermaid_code += '    LB["Load Balancer<br/>Entry Point"]\n'
    
    # Master-eligible nodes
    master_nodes_added = []
    for i, node in enumerate(all_master_eligible):
        node_id = f"M{i+1}"
        master_nodes_added.append(node_id)
        
        # Determine node type and specs
        optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
        heap_size = optimal['heap_size']
        
        if node['roles'] == ['master']:
            # Master-only node - with line breaks for readability
            node_label = f"{node['name']}<br/>Master Only<br/>{node['cpu_cores']}C {node['ram_gb']}GB<br/>Heap: {heap_size}"
            mermaid_code += f'    {node_id}["{node_label}"]\n'
        else:
            # Master + Data node - with line breaks for readability
            capacity = int(optimal['capacity_estimates']['data_capacity_gb'])
            node_label = f"{node['name']}<br/>Master + Data<br/>{node['cpu_cores']}C {node['ram_gb']}GB<br/>Heap: {heap_size}<br/>~{capacity}GB Data"
            mermaid_code += f'    {node_id}["{node_label}"]\n'
    
    # Data-only nodes
    data_nodes_added = []
    for i, node in enumerate(data_only_nodes):
        node_id = f"D{i+1}"
        data_nodes_added.append(node_id)
        
        optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
        heap_size = optimal['heap_size']
        capacity = int(optimal['capacity_estimates']['data_capacity_gb'])
        search_threads = optimal['thread_pools']['search']
        
        node_label = f"{node['name']}<br/>Data + Ingest<br/>{node['cpu_cores']}C {node['ram_gb']}GB<br/>Heap: {heap_size}<br/>~{capacity}GB Data<br/>{search_threads} Search Threads"
        mermaid_code += f'    {node_id}["{node_label}"]\n'
    
    # Connect to master nodes
    if len(all_master_eligible) > 1:
        # Via load balancer to distribute requests
        for master_id in master_nodes_added:
            mermaid_code += f"    LB --> {master_id}\n"
    
    # Master coordination (if multiple masters)
    if len(master_nodes_added) > 1:
        for i in range(len(master_nodes_added)):
            for j in range(i+1, len(master_nodes_added)):
                mermaid_code += f"    {master_nodes_added[i]} -.-> {master_nodes_added[j]}\n"
    
    # Master to data connections (for mixed master+data nodes)
    master_data_ids = []
    for i, node in enumerate(master_data_nodes):
        master_data_ids.append(f"M{all_master_eligible.index(node)+1}")
    
    # Pure data node connections
    if data_nodes_added:
        # Masters coordinate with data nodes
        for master_id in master_nodes_added:
            for data_id in data_nodes_added:
                mermaid_code += f"    {master_id} -.-> {data_id}\n"
    
    # Data replication between data nodes (if multiple data nodes exist)
    total_data_node_ids = master_data_ids + data_nodes_added
    if len(total_data_node_ids) > 1:
        mermaid_code += "\n    %% Data replication between data nodes\n"
        for i in range(len(total_data_node_ids)):
            for j in range(i+1, len(total_data_node_ids)):
                mermaid_code += f"    {total_data_node_ids[i]} <-.-> {total_data_node_ids[j]}\n"
    
    # Add styling
    mermaid_code += "\n    %% Styling\n"
    mermaid_code += "    classDef masterNode fill:#e1f5fe,stroke:#01579b,stroke-width:2px\n"
    mermaid_code += "    classDef dataNode fill:#f3e5f5,stroke:#4a148c,stroke-width:2px\n"
    mermaid_code += "    classDef mixedNode fill:#e8f5e8,stroke:#1b5e20,stroke-width:2px\n"
    mermaid_code += "    classDef lb fill:#fce4ec,stroke:#880e4f,stroke-width:2px\n"
    
    # Apply styling
    if len(all_master_eligible) > 1:
        mermaid_code += "    class LB lb\n"
    
    # Style master-only nodes
    for i, node in enumerate(master_only_nodes):
        if node in all_master_eligible:
            master_index = all_master_eligible.index(node)
            mermaid_code += f"    class M{master_index+1} masterNode\n"
    
    # Style master+data nodes
    for i, node in enumerate(master_data_nodes):
        if node in all_master_eligible:
            master_index = all_master_eligible.index(node)
            mermaid_code += f"    class M{master_index+1} mixedNode\n"
    
    # Style data-only nodes
    for i, node_id in enumerate(data_nodes_added):
        mermaid_code += f"    class {node_id} dataNode\n"
    
    return mermaid_code

def generate_request_flow_diagram(config):
    """Generate diagram showing request flow patterns"""
    nodes = config['nodes']
    
    if not nodes:
        return "graph TD\n    A[No nodes configured]"
    
    # Categorize nodes
    master_eligible = [n for n in nodes if 'master' in n['roles']]
    data_nodes = [n for n in nodes if 'data' in n['roles']]
    
    mermaid_code = "graph TD\n"
    
    # Master nodes handle different request types
    if master_eligible:
        master_node = master_eligible[0]  # Use first master for example
        optimal_master = calculate_optimal_settings(master_node['cpu_cores'], master_node['ram_gb'], master_node['roles'])
        heap_size = optimal_master['heap_size']
        
        if master_node['roles'] == ['master']:
            master_label = f"{master_node['name']}<br/>Master Only<br/>{master_node['cpu_cores']}C {master_node['ram_gb']}GB<br/>Entry Point"
        else:
            capacity = int(optimal_master['capacity_estimates']['data_capacity_gb'])
            master_label = f"{master_node['name']}<br/>Master + Data<br/>{master_node['cpu_cores']}C {master_node['ram_gb']}GB<br/>Entry Point"
            
        mermaid_code += f'    Master["{master_label}"]\n'
    
    # Data nodes handle actual data operations - dynamic for any number of nodes
    data_node_ids = []
    if data_nodes:
        for i, node in enumerate(data_nodes):  # Show ALL data nodes
            optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
            capacity = int(optimal['capacity_estimates']['data_capacity_gb'])
            
            node_id = f"Data{i+1}"
            data_node_ids.append(node_id)
            node_label = f"{node['name']}<br/>Data Node<br/>~{capacity}GB Capacity"
            mermaid_code += f'    {node_id}["{node_label}"]\n'
            
            if master_eligible:
                mermaid_code += f"    Master --> {node_id}\n"
    
    # Add request flow details with labels - dynamic for all data nodes
    mermaid_code += "\n    %% Request Flow - Master coordinates with Data Nodes\n"
    if data_nodes and data_node_ids:
        # Master distributes queries to all data nodes
        for node_id in data_node_ids:
            mermaid_code += f'    Master -.->|"Distribute Queries"| {node_id}\n'
        
        # Data nodes return results to master
        for node_id in data_node_ids:
            mermaid_code += f'    {node_id} -.->|"Return Results"| Master\n'
    
    # Styling
    mermaid_code += "\n    classDef master fill:#e8f5e8,stroke:#1b5e20,stroke-width:2px\n"
    mermaid_code += "    classDef data fill:#f3e5f5,stroke:#4a148c,stroke-width:2px\n"
    
    mermaid_code += "    class Master master\n"
    
    # Apply styling to all data nodes dynamically
    for node_id in data_node_ids:
        mermaid_code += f"    class {node_id} data\n"
    
    return mermaid_code

# Create main layout - sidebar is handled by st.sidebar, main content uses full width
main_col = st.container()

# Right sidebar for configuration
with st.sidebar:
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
        
    with st.expander("🐳 **Dedicated Server Memory Formula**", expanded=False):
        st.markdown("""
        **🎯 NEW: Optimized for Dedicated Elasticsearch Servers**
        
        Your RAM inputs follow Elasticsearch best practices for dedicated servers:
        
        **Core Formulas:**
        ```
        ES_Heap = min(Total_RAM × 0.50, 31GB)  # ES Best Practice
        Container_Limit = Total_RAM - OS_Reserve  # Dedicated server
        ```
        
        **Minimal OS Reserves (Dedicated ES Servers):**
        - ≤16GB system: 1.0GB reserve
        - 16-64GB system: 1.5GB reserve  
        - ≥64GB system: 2.0GB reserve
        
        **Example: 32GB Dedicated Server:**
        - **Total RAM**: 32GB
        - **ES Heap**: 32GB × 0.50 = 16GB (follows ES 50% rule)
        - **OS Reserve**: 1.5GB (minimal for dedicated server)
        - **Container Limit**: 32GB - 1.5GB = 30.5GB
        - **Off-Heap**: 30.5GB - 16GB = 14.5GB (excellent for file cache!)
        
        **Your 31.34GB Server:**
        - **ES Heap**: 15.7GB (50% of system RAM)
        - **Container Limit**: ~29.8GB (leaves 1.5GB for OS)
        - **Off-Heap**: ~14.1GB (great for search performance)
        
        ✅ **Benefits**: Maximum ES performance, minimal OS waste, follows ES documentation
        """)    

    tab1, tab2, tab3, tab4 = st.tabs(["🔧 Cluster Setup", "🖥️ Node Configuration", "📊 Visualize (Experimental)", "📄 Generate Files"])

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
            
            # Node count using number input for reliable increment/decrement
            new_node_count = st.number_input(
                "🖥️ Number of Nodes",
                min_value=1,
                max_value=20,
                value=st.session_state.node_count,
                step=1,
                help="Select the number of nodes for your cluster (1-20)"
            )
            
            # Update node count and sync nodes when changed
            if new_node_count != st.session_state.node_count:
                st.session_state.node_count = new_node_count
                ensure_nodes_sync()
                st.rerun()
        
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
                                        
                                        # Quick metrics with corrected memory calculations
                                        mem_limits = optimal['production_memory_limits']
                                        st.metric("🧠 Heap Size", optimal['heap_size'], f"50% of {node['ram_gb']}GB RAM")
                                        st.metric("🐳 Container Limit", f"{mem_limits['container_limit_gb']}GB", f"{round((mem_limits['container_limit_gb']/node['ram_gb'])*100, 1)}% of system RAM")
                                        st.metric("🔍 Search Threads", optimal['thread_pools']['search'])
                                        st.metric("📊 Est. Capacity", f"~{optimal['capacity_estimates']['data_capacity_gb']:,}GB")
                                        
                                        # Capacity overview
                                        st.markdown("**Performance Estimates:**")
                                        st.text(f"• Concurrent Searches: ~{optimal['capacity_estimates']['concurrent_searches']:,}")
                                        st.text(f"• Indexing Rate: ~{optimal['capacity_estimates']['indexing_rate_docs_per_sec']:,}/sec")
                                    
                                    with tab2:
                                        st.markdown(f"**Node Type:** {node_type} | **Hardware:** {node['cpu_cores']} cores, {node['ram_gb']}GB RAM")
                                        
                                        # Production Memory Calculation
                                        st.markdown("##### 💾 Dedicated Server Memory Formula")
                                        mem_limits = optimal['production_memory_limits']
                                        st.info(f"""
                                        **Elasticsearch Best Practice Applied:**
                                        - **System RAM**: {node['ram_gb']}GB (your input)
                                        - **ES Heap**: {mem_limits['heap_size_gb']}GB (50% of system RAM, max 31GB)
                                        - **OS Reserve**: {mem_limits['os_reserve_gb']}GB (minimal for dedicated server)
                                        - **Container Limit**: {mem_limits['container_limit_gb']}GB (Docker memory limit)
                                        - **Off-Heap Memory**: {mem_limits['off_heap_gb']}GB (file cache, buffers)
                                        
                                        **Memory Efficiency**: {round((mem_limits['container_limit_gb']/node['ram_gb'])*100, 1)}% of RAM used by Elasticsearch
                                        """)
                                        
                                        # Memory Configuration - vertical layout to avoid nesting
                                        st.markdown("##### ⚙️ Elasticsearch Memory Settings")
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
        st.header("📊 Cluster Architecture Visualization")
        
        if len(st.session_state.cluster_config['nodes']) == 0:
            st.warning("⚠️ Please configure at least one node in the Node Configuration tab to see the visualization.")
            st.info("""
            **To get started:**
            1. Go to the "🔧 Cluster Setup" tab and set your cluster name and node count
            2. Configure your nodes in the "🖥️ Node Configuration" tab
            3. Return here to see your cluster architecture visualized!
            """)
        else:
            # Calculate cluster stats
            nodes = st.session_state.cluster_config['nodes']
            master_eligible = [n for n in nodes if 'master' in n['roles']]
            data_nodes = [n for n in nodes if 'data' in n['roles']]
            master_only = [n for n in nodes if n['roles'] == ['master']]
            master_data = [n for n in nodes if 'master' in n['roles'] and 'data' in n['roles']]
            data_only = [n for n in nodes if 'data' in n['roles'] and 'master' not in n['roles']]
            
            # Cluster overview
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Nodes", len(nodes))
            with col2:
                st.metric("Master Eligible", len(master_eligible))
            with col3:
                st.metric("Data Nodes", len(data_nodes))
            with col4:
                min_masters = calculate_minimum_master_nodes(len(nodes), len(master_eligible))
                st.metric("Min Masters", min_masters)
            
            # Node breakdown
            with st.expander("📊 **Cluster Composition Breakdown**", expanded=False):
                st.markdown(f"""
                **Node Role Distribution:**
                - 👑 **Master-only nodes**: {len(master_only)} (dedicated cluster coordination)
                - 👑💾 **Master + Data nodes**: {len(master_data)} (coordination + data storage)
                - 💾 **Data-only nodes**: {len(data_only)} (pure data storage and search)
                
                **Cluster Characteristics:**
                - **Master-eligible nodes**: {len(master_eligible)} out of {len(nodes)} total
                - **Split-brain protection**: Requires {min_masters} active masters
                - **Data storage capacity**: {len(data_nodes)} nodes handling data operations
                """)
                
                # Performance summary
                total_capacity = 0
                total_search_threads = 0
                total_cpu = 0
                total_ram = 0
                
                for node in data_nodes:
                    optimal = calculate_optimal_settings(node['cpu_cores'], node['ram_gb'], node['roles'])
                    total_capacity += optimal['capacity_estimates']['data_capacity_gb']
                    total_search_threads += optimal['thread_pools']['search']
                    total_cpu += node['cpu_cores']
                    total_ram += node['ram_gb']
                
                if total_capacity > 0:
                    st.markdown(f"""
                    **Estimated Cluster Performance:**
                    - **Total Data Capacity**: ~{total_capacity:,.0f}GB
                    - **Search Thread Pool**: {total_search_threads} threads across all data nodes
                    - **Total Hardware**: {total_cpu} CPU cores, {total_ram}GB RAM
                    """)
            
            # Visualization options
            st.subheader("🎨 Visualization Options")
            
            viz_type = st.radio(
                "Select visualization type:",
                options=["cluster_architecture", "request_flow"],
                format_func=lambda x: "🏗️ Cluster Architecture" if x == "cluster_architecture" else "🔄 Request Flow",
                horizontal=True,
                help="Choose between cluster architecture view or request flow diagram"
            )
            
            # Generate and display visualization
            if viz_type == "cluster_architecture":
                st.subheader("🏗️ Cluster Architecture")
                st.markdown("""
                This diagram shows your cluster's internal architecture, node types, and their relationships.
                
                **Legend:**
                - ⚖️ **Load Balancer**: Distributes requests (shown when multiple master nodes exist)
                - 👑 **Master Nodes**: Coordinate cluster operations and serve as entry points (blue)
                - 👑💾 **Master+Data Nodes**: Handle both coordination and data (green)
                - 💾 **Data Nodes**: Store and search data (purple)
                
                **Connection Types:**
                - **Solid lines** (→): Request routing
                - **Dotted lines** (-.->): Cluster coordination
                - **Bidirectional** (<-.->): Data replication
                """)
                
                cluster_diagram = generate_cluster_visualization(st.session_state.cluster_config)
                # Use unique key based on node count and configuration to force refresh
                diagram_key = f"cluster_arch_{len(st.session_state.cluster_config['nodes'])}_{hash(str(st.session_state.cluster_config))}"
                stmd.st_mermaid(cluster_diagram, height=600, key=diagram_key)
                
            else:  # request_flow
                st.subheader("🔄 Request Flow")
                st.markdown("""
                This diagram shows how requests flow between nodes within your cluster.
                
                **Internal Flow:**
                - **Master Node**: Acts as the coordinator and entry point for requests
                - **Data Nodes**: Handle actual data operations (search, indexing, storage)
                
                **Request Processing:**
                1. Master node receives and coordinates requests
                2. Master distributes queries to appropriate data nodes
                3. Data nodes process operations and return results
                4. Master aggregates responses from data nodes
                
                **Node Communication:**
                - Master nodes coordinate all cluster operations
                - Data nodes communicate results back to master nodes
                - All communication happens within the cluster network
                """)
                
                flow_diagram = generate_request_flow_diagram(st.session_state.cluster_config)
                # Use unique key based on node count and configuration to force refresh
                flow_key = f"request_flow_{len(st.session_state.cluster_config['nodes'])}_{hash(str(st.session_state.cluster_config))}"
                stmd.st_mermaid(flow_diagram, height=600, key=flow_key)
            
            # Architecture insights
            st.subheader("💡 Architecture Insights")
            
            insights = []
            
            # Single point of failure check
            if len(master_eligible) == 1:
                insights.append("⚠️ **Single Master**: Your cluster has only one master-eligible node. This creates a single point of failure for cluster coordination.")
            elif len(master_eligible) == 2:
                insights.append("❌ **Split-brain Risk**: Two master nodes can cause split-brain scenarios. Consider using 1, 3, or 5 master nodes.")
            else:
                insights.append(f"✅ **Master High Availability**: {len(master_eligible)} master-eligible nodes provide good fault tolerance.")
            
            # Data node distribution
            if len(data_nodes) == 1:
                insights.append("⚠️ **Single Data Node**: All data is stored on one node. Consider adding more data nodes for redundancy and performance.")
            elif len(data_nodes) >= 3:
                insights.append(f"✅ **Data Distribution**: {len(data_nodes)} data nodes allow for good data distribution and search performance.")
            
            # Resource allocation
            if len(master_only) > 0:
                insights.append(f"✅ **Dedicated Masters**: {len(master_only)} dedicated master nodes optimize cluster coordination performance.")
            
            if len(master_data) > 0 and len(data_only) > 0:
                insights.append("💡 **Hybrid Architecture**: Mix of master+data and data-only nodes provides flexibility.")
            
            # Performance insights
            if total_capacity > 0:
                capacity_per_node = total_capacity / len(data_nodes)
                if capacity_per_node > 1000:  # 1TB per node
                    insights.append(f"💾 **High Capacity**: ~{capacity_per_node:,.0f}GB average per data node indicates a high-capacity cluster.")
                
                if total_search_threads > 50:
                    insights.append(f"⚡ **High Performance**: {total_search_threads} total search threads indicate strong search performance capability.")
            
            for insight in insights:
                if insight.startswith('✅'):
                    st.success(insight)
                elif insight.startswith('⚠️'):
                    st.warning(insight)
                elif insight.startswith('❌'):
                    st.error(insight)
                else:
                    st.info(insight)

    with tab4:
        st.header("📄 Generate Configuration Files")
        
        # Mode selection
        st.subheader("🎯 Deployment Mode")
        
        col1, col2 = st.columns(2)
        with col1:
            deployment_mode = st.radio(
                "🚀 Select Deployment Mode",
                options=["development", "production"],
                format_func=lambda x: "🛠️ Development Mode" if x == "development" else "🏭 Production Mode",
                help="Choose between development (single file) or production (individual files) deployment",
                index=0  # Default to development mode for easier testing
            )
            
            # Visual indicator for selected mode
            if deployment_mode == "development":
                st.success("🛠️ **Development Mode Selected**")
            else:
                st.info("🏭 **Production Mode Selected**")
        
        with col2:
            if deployment_mode == "development":
                st.info("""
                **🛠️ Development Mode:**
                - Single `docker-compose.yml` file
                - All containers in one file for easy local testing
                - Containers communicate via Docker network names
                - No extra host mappings needed
                - Perfect for local development and testing
                """)
            else:
                st.info("""
                **🏭 Production Mode:**
                - Individual Docker Compose files per node
                - Host-to-host communication with extra hosts
                - Advanced initialization and run scripts
                - Suitable for distributed deployment
                - Production-ready with health checks
                """)
        
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
                
                # Generate cluster files based on mode
                if deployment_mode == "development":
                    st.info("🛠️ **Development Package**: Single Docker Compose file with simple startup script!")
                    
                    with st.expander("📋 **What's Included in Development Package**", expanded=False):
                        st.markdown("""
                        **Development Mode Files:**
                        - `docker-compose.yml` - Single file with all nodes
                        - `start.sh` - Simple startup script with health checks
                        - `README.md` - Development-focused documentation
                        
                        **Key Features:**
                        - ✅ **Single file deployment**: All containers in one Docker Compose
                        - ✅ **Container networking**: No extra hosts needed
                        - ✅ **Quick startup**: Simple `./start.sh` command
                        - ✅ **Easy debugging**: Standard Docker Compose commands
                        - ✅ **Local development**: Perfect for testing and development
                        """)
                else:
                    st.info("🏭 **Production Package**: Individual Docker Compose files + comprehensive init scripts!")
                    
                    with st.expander("📋 **What's Included in Production Package**", expanded=False):
                        st.markdown("""
                        **📁 Ultra-Streamlined Folder Structure:**
                        ```
                        elasticsearch-cluster/
                        ├── README.md                    # Complete documentation
                        ├── cluster-init.sh              # Global system validation
                        ├── start-all.sh                 # Start all nodes automatically
                        ├── stop-all.sh                  # Stop all nodes
                        └── nodes/
                            ├── els01/                   # Node 1 folder
                            │   ├── docker-compose.yml   # Everything: ES + JVM settings
                            │   ├── run.sh               # Start this node with validation
                            │   └── init.sh              # Legacy init script
                            ├── els02/                   # Node 2 folder
                            └── ... (additional nodes)
                        ```
                        
                        **✨ Ultra-Streamlined & Zero Redundancy:**
                        - **Everything in docker-compose**: All ES + JVM settings via environment variables
                        - **No separate config files**: Zero redundancy, single source of truth
                        - **Version-optimized**: ES 6.x/7.x/8.x settings built into compose files
                        - **Production memory limits**: ES heap = 50% of system RAM (max 31GB)
                        - **Maximum simplicity**: Just 3 files per node
                        - **Container-native**: Pure Docker/Kubernetes best practices
                        """)
                
                # Generate button text based on mode
                button_text = "🛠️ Generate Development Files" if deployment_mode == "development" else "🏭 Generate Production Files"
                
                if st.button(button_text, use_container_width=True, type="primary"):
                    # Generate files based on mode
                    if deployment_mode == "development":
                        cluster_files = generate_development_files(st.session_state.cluster_config)
                    else:
                        cluster_files = generate_cluster_files(st.session_state.cluster_config)
                    
                    # Create a ZIP file with all cluster files
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                        for filename, content in cluster_files.items():
                            zip_file.writestr(filename, content)
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cluster_name = st.session_state.cluster_config['cluster_name'].replace(' ', '_').lower()
                    mode_suffix = "dev" if deployment_mode == "development" else "prod"
                    
                    # Download button text based on mode
                    download_text = "📦 Download Development Package" if deployment_mode == "development" else "📦 Download Production Package (Organized Folders)"
                    help_text = "Contains single Docker Compose file and simple startup script" if deployment_mode == "development" else "Contains organized folders per node with ultra-streamlined Docker Compose files (no config redundancy)"
                    
                    st.download_button(
                        label=download_text,
                        data=zip_buffer.getvalue(),
                        file_name=f"{cluster_name}_elasticsearch_{mode_suffix}_{timestamp}.zip",
                        mime="application/zip",
                        use_container_width=True,
                        help=help_text
                    )
                
                # Preview generated files
                preview_text = "👁️ Preview Development Files" if deployment_mode == "development" else "👁️ Preview Production Files"
                if st.checkbox(preview_text):
                    nodes = st.session_state.cluster_config['nodes']
                    
                    if len(nodes) > 0:
                        if deployment_mode == "development":
                            # Development mode preview - show the single docker compose and startup script
                            st.markdown("### 🛠️ Development Mode Files Preview")
                            
                            dev_tab1, dev_tab2, dev_tab3 = st.tabs(["🐳 Docker Compose", "🚀 Startup Script", "📖 README"])
                            
                            with dev_tab1:
                                st.subheader("🐳 docker-compose.yml")
                                st.markdown("**Purpose**: Single file containing all cluster nodes")
                                compose_content = generate_development_docker_compose(st.session_state.cluster_config)
                                st.code(compose_content, language='yaml')
                                st.info("✅ **Development optimized**: Container networking, no extra hosts, simplified configuration")
                            
                            with dev_tab2:
                                st.subheader("🚀 start.sh")
                                st.markdown("**Purpose**: Simple startup script with health checks")
                                dev_files = generate_development_files(st.session_state.cluster_config)
                                st.code(dev_files["start.sh"], language='bash')
                                st.success("✅ **Features**: Docker checks, directory creation, cluster startup, health monitoring")
                            
                            with dev_tab3:
                                st.subheader("📖 README.md")
                                st.markdown("**Purpose**: Development-focused documentation")
                                # Use the same dev_files variable to avoid regenerating
                                st.code(dev_files["README.md"], language='markdown')
                                st.info("✅ **Content**: Quick start guide, troubleshooting, development tips")
                            
                            st.markdown("---")
                            st.success(f"""
                            📦 **Development Package Summary**:
                            - `docker-compose.yml` - All {len(nodes)} nodes in single file
                            - `start.sh` - Simple startup script
                            - `README.md` - Development documentation
                            
                            **Quick Start**: Extract files and run `./start.sh`
                            """)
                        
                        else:
                            # Production mode preview - show organized folder structure
                            st.markdown("### 🏭 Production Mode Files Preview (Organized Structure)")
                            
                            # Create main tabs for global and node-specific files
                            global_tab, nodes_tab = st.tabs(["🌐 Global Scripts", "📁 Node Folders"])
                            
                            with global_tab:
                                st.markdown("### 🌐 Cluster-wide Management Scripts")
                                
                                global_file_tabs = st.tabs(["📋 README", "🔍 Cluster Init", "🚀 Start All", "🛑 Stop All"])
                                
                                with global_file_tabs[0]:
                                    st.subheader("📋 README.md")
                                    st.markdown("**Purpose**: Complete cluster documentation with organized structure")
                                    cluster_files = generate_cluster_files(st.session_state.cluster_config)
                                    st.code(cluster_files["README.md"], language='markdown')
                                    st.info("✅ **Content**: Organized structure, quick start, management commands")
                                
                                with global_file_tabs[1]:
                                    st.subheader("🔍 cluster-init.sh")
                                    st.markdown("**Purpose**: Global system validation for entire cluster")
                                    st.code(cluster_files["cluster-init.sh"], language='bash')
                                    st.success("✅ **Features**: Docker validation, system limits, directory structure, port availability")
                                
                                with global_file_tabs[2]:
                                    st.subheader("🚀 start-all.sh")
                                    st.markdown("**Purpose**: Start all nodes sequentially with health checks")
                                    st.code(cluster_files["start-all.sh"], language='bash')
                                    st.success("✅ **Features**: Sequential startup, health monitoring, cluster status")
                                
                                with global_file_tabs[3]:
                                    st.subheader("🛑 stop-all.sh")
                                    st.markdown("**Purpose**: Stop all nodes in proper order")
                                    st.code(cluster_files["stop-all.sh"], language='bash')
                                    st.info("✅ **Features**: Reverse order shutdown, cleanup validation")
                            
                            with nodes_tab:
                                st.markdown("### 📁 Individual Node Folders")
                                
                                # Show node folder structure
                                node_tab_names = [f"📂 {node['name']}/" for node in nodes]
                                node_folder_tabs = st.tabs(node_tab_names)
                                
                                for i, node in enumerate(nodes):
                                    with node_folder_tabs[i]:
                                        st.markdown(f"### 📂 nodes/{node['name']}/")
                                        st.markdown(f"**Roles**: {', '.join(node['roles']).title()} | **Hardware**: {node['cpu_cores']} cores, {node['ram_gb']}GB RAM")
                                        
                                        # Create tabs for different file types in this node folder
                                        file_tabs = st.tabs(["🐳 Docker Compose", "🚀 Run Script", "📋 Node README"])
                                        
                                        with file_tabs[0]:
                                            st.subheader("🐳 docker-compose.yml")
                                            st.markdown(f"**Location**: `nodes/{node['name']}/docker-compose.yml`")
                                            compose_content = generate_individual_docker_compose(node, st.session_state.cluster_config)
                                            st.code(compose_content, language='yaml')
                                            st.info("✅ **Production ready**: Extra hosts mapping, individual networking, comprehensive settings")
                                        
                                        with file_tabs[1]:
                                            st.subheader("🚀 run.sh")
                                            st.markdown(f"**Location**: `nodes/{node['name']}/run.sh`")
                                            st.markdown("**Purpose**: Start this specific node with pre-flight validation")
                                            run_content = generate_node_run_script_organized(node, st.session_state.cluster_config)
                                            st.code(run_content, language='bash')
                                            st.success("✅ **Features**: Pre-flight validation, directory auto-creation, health monitoring")
                                        
                                        with file_tabs[2]:
                                            st.subheader("📋 README.md")
                                            st.markdown(f"**Location**: `nodes/{node['name']}/README.md`")
                                            st.markdown("**Purpose**: Node-specific documentation and management guide")
                                            node_readme = generate_node_readme(node, st.session_state.cluster_config)
                                            st.code(node_readme, language='markdown')
                                            st.info("✅ **Content**: Node details, management commands, troubleshooting")
                                        
                                        # Show folder summary
                                        st.markdown("---")
                                        st.success(f"""
                                        📦 **Folder: nodes/{node['name']}/** (Ultra-Streamlined)
                                        - `docker-compose.yml` - Everything: ES + JVM settings
                                        - `run.sh` - Start this node with validation
                                        - `init.sh` - Legacy initialization script
                                        - `README.md` - Node-specific documentation
                                        - `data/`, `logs/`, `backups/` - Volume directories (auto-created)
                                        
                                        **Quick Start**: `cd nodes/{node['name']} && ./run.sh`
                                        """)
                            
                            st.markdown("---")
                            st.success(f"""
                            📦 **Complete Ultra-Streamlined Package Summary**:
                            - **Global scripts**: cluster-init.sh, start-all.sh, stop-all.sh, README.md
                            - **Organized folders**: {len(nodes)} node folders under `nodes/` directory
                            - **Per-node files**: Docker Compose (with everything), run scripts, documentation
                            - **Zero redundancy**: All ES + JVM settings in docker-compose files
                            
                            **Quick Start**: 
                            1. Extract and run `./cluster-init.sh` (validate system)
                            2. Run `./start-all.sh` (start entire cluster)
                            3. Or individual: `cd nodes/<node-name> && ./run.sh`
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
