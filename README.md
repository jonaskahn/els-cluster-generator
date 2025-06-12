# Elasticsearch Cluster Configuration Generator

A web-based tool for generating production-ready Elasticsearch clusters with Docker Compose.

## Features

- **Node Types**: Master-only, Master+Data+Ingest, Data+Ingest only
- **Hardware Optimization**: Automatic settings based on CPU cores and RAM
- **Split-brain Prevention**: Proper master node calculation
- **Production Settings**: 50+ optimized configurations
- **Individual Files**: Separate Docker Compose and init scripts per node
- **Network Resolution**: Extra hosts configuration for cluster communication

## Requirements

- Python 3.7+
- Docker and Docker Compose

## Installation

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Start the application:
   ```bash
   streamlit run run.py
   ```

2. Open your browser to `http://localhost:8501`

3. Configure your cluster:
   - Set cluster name and domain
   - Choose number of nodes
   - Configure node roles and hardware
   - Enable X-Pack features as needed

4. Generate and download cluster files

## Generated Files

For each node, the tool generates:
- `docker-compose-{node}.yml` - Complete Docker Compose configuration
- `init-{node}.sh` - Initialization script
- `README.md` - Deployment instructions

## Node Types

### Master Only
- Dedicated master node
- Lightweight resource allocation
- Recommended for large clusters (6+ nodes)

### Master + Data + Ingest
- Full-featured node
- Handles all operations
- Recommended for small-medium clusters

### Data + Ingest Only
- Data storage and processing
- No master election participation
- Scales horizontally for large datasets

## Configuration Management

- **Save Config**: Download complete cluster configuration as JSON
- **Load Config**: Upload previously saved configurations
- **Validation**: Real-time checks for split-brain prevention

## Production Deployment

1. Extract downloaded cluster files
2. Make init scripts executable: `chmod +x init-*.sh`
3. Run nodes individually: `./init-{node}.sh`
4. Or deploy all at once using the provided instructions

## System Requirements

### Minimum
- 2 CPU cores per node
- 4GB RAM per node
- Docker 20.10+

### Recommended
- 8+ CPU cores per node
- 32GB+ RAM per node
- SSD storage

## License

This project is provided as-is for educational and production use.

## Support

For issues or questions, check the generated README.md file included with your cluster configuration for specific deployment guidance. 