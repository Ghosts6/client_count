import uvicorn
import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    # Get host and port from environment or use defaults
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    
    logger.info(f"Starting AP Monitor API on {host}:{port}")
    
    # Run the FastAPI application with uvicorn
    uvicorn.run("app.main:app", host=host, port=port, reload=True)