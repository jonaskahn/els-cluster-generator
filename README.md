# Elasticsearch Cluster Generator

A web-based tool for generating production-ready Elasticsearch cluster configurations with Docker Compose.

## Features

- **Smart Configuration**: Automatic optimization based on CPU cores and RAM
- **Node Role Management**: Support for Master, Data, and Ingest nodes
- **Production Ready**: Pre-configured settings for production environments
- **Split-brain Prevention**: Automatic calculation of minimum master nodes
- **Visualization**: Cluster topology and request flow diagrams
- **Configuration Management**: Save/load cluster configurations

## Quick Start

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Run the application:

   ```bash
   streamlit run streamlit_app.py
   ```

3. Access the web interface at `http://localhost:8501`

## Generated Output

The tool generates two different output structures depending on the deployment mode:

### Development Mode (Single File)

```
elasticsearch-cluster-dev/
├── docker-compose.yml          # Single file with all nodes
├── start.sh                    # Simple startup script
└── README.md                   # Development documentation
```

**Features:**

- All nodes in one Docker Compose file
- Container networking (no host mapping needed)
- Quick startup and easy debugging
- Perfect for local development and testing

### Production Mode (Organized Structure)

```
elasticsearch-cluster-prod/
├── README.md                   # Complete cluster documentation
├── cluster-init.sh             # Global system validation
├── start-all.sh                # Start all nodes sequentially
├── stop-all.sh                 # Stop all nodes
└── nodes/
    ├── els01/                  # Node 1 folder
    │   ├── docker-compose.yml  # Individual node configuration
    │   ├── run.sh              # Node startup with validation
    │   └── README.md           # Node-specific documentation
    ├── els02/                  # Node 2 folder
    │   ├── docker-compose.yml
    │   ├── run.sh
    │   └── README.md
    └── els03/                  # Node 3 folder (if configured)
        ├── docker-compose.yml
        ├── run.sh
        └── README.md
```

**Features:**

- Ultra-streamlined: All ES + JVM settings in docker-compose.yml
- Zero configuration redundancy
- Individual node folders for easy management
- Production-ready with comprehensive validation
- Scalable for large clusters

## System Requirements

- Python 3.7+
- Docker and Docker Compose
- Minimum 2 CPU cores per node
- Minimum 4GB RAM per node

## Configuration Options

- Cluster name and domain settings
- Node count and roles
- Hardware specifications
- Elasticsearch version
- X-Pack features
- Network settings

## Deployment

### Development Mode Deployment

1. **Extract and prepare files:**

   ```bash
   unzip elasticsearch-cluster-dev_*.zip
   cd elasticsearch-cluster-dev/
   ```

2. **Make scripts executable:**

   ```bash
   chmod +x start.sh
   ```

3. **Start the cluster:**

   ```bash
   ./start.sh
   ```

   Or manually with Docker Compose:

   ```bash
   docker-compose up -d
   ```

4. **Verify cluster health:**
   ```bash
   curl http://localhost:9200/_cluster/health?pretty
   curl http://localhost:9200/_cat/nodes?v
   ```

### Production Mode Deployment

#### Option 1: Automated Cluster Deployment (Recommended)

1. **Extract and prepare files:**

   ```bash
   unzip elasticsearch-cluster-prod_*.zip
   cd elasticsearch-cluster-prod/
   ```

2. **Make all scripts executable:**

   ```bash
   chmod +x cluster-init.sh start-all.sh stop-all.sh
   chmod +x nodes/*/run.sh
   ```

3. **Validate system requirements:**

   ```bash
   ./cluster-init.sh
   ```

4. **Start entire cluster:**
   ```bash
   ./start-all.sh
   ```

#### Option 2: Individual Node Deployment

1. **Prepare and validate system:**

   ```bash
   ./cluster-init.sh
   ```

2. **Deploy nodes individually:**

   ```bash
   # Start first node
   cd nodes/els01/
   ./run.sh

   # Start second node (in new terminal)
   cd ../els02/
   ./run.sh

   # Continue for additional nodes...
   ```

#### Option 3: Manual Docker Compose

1. **Navigate to each node directory:**

   ```bash
   cd nodes/els01/
   docker-compose up -d

   cd ../els02/
   docker-compose up -d

   # Repeat for all nodes...
   ```

### Post-Deployment Verification

```bash
# Check cluster health
curl http://NODE_IP:9200/_cluster/health?pretty

# List all nodes
curl http://NODE_IP:9200/_cat/nodes?v

# Check cluster settings
curl http://NODE_IP:9200/_cluster/settings?pretty
```

### Management Commands

```bash
# Stop entire cluster (production mode)
./stop-all.sh

# Stop development cluster
docker-compose down

# View logs (development)
docker-compose logs -f

# View logs (production - specific node)
cd nodes/els01/ && docker-compose logs -f
```

## License

This project is provided as-is for educational and production use.
