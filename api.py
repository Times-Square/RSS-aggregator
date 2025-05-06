from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="News API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# MongoDB connection
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
try:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client.news_db
    news_collection = db.news
    logger.info("Successfully connected to MongoDB")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

@app.get("/")
async def root():
    return {"message": "Welcome to News API", "status": "running"}

@app.get("/api/news")
async def get_latest_news():
    try:
        # Log the request
        logger.info("Fetching latest news")
        
        # Get total count of documents
        total_docs = await news_collection.count_documents({})
        logger.info(f"Total documents in collection: {total_docs}")

        # Get the latest 5 news items
        cursor = news_collection.find({}) \
            .sort("pubDate", -1) \
            .limit(5)
        
        news = []
        async for document in cursor:
            # Convert datetime to string for JSON serialization
            document["pubDate"] = document["pubDate"].isoformat()
            # Remove MongoDB _id field
            document.pop("_id")
            news.append(document)
        
        logger.info(f"Retrieved {len(news)} news items")
        
        if not news:
            logger.warning("No news items found in database")
            return JSONResponse(
                status_code=404,
                content={"message": "Наразі немає новин у базі даних. Зачекайте, поки RSS парсер завантажить нові новини."}
            )
        
        return news
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Помилка при отриманні новин: {str(e)}"
        )

@app.get("/api/health")
async def health_check():
    try:
        # Check MongoDB connection
        await db.command("ping")
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": "disconnected", "error": str(e)}
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True) 