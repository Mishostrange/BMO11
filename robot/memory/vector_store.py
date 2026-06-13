import logging
import chromadb
from datetime import datetime
from typing import List, Dict, Any
from robot.config.settings import settings

logger = logging.getLogger(__name__)

class VectorMemory:
    """Semantic vector memory using ChromaDB for fuzzy matching and similarity search."""
    
    def __init__(self):
        try:
            self.client = chromadb.PersistentClient(path=settings.db.VECTOR_DB_PATH)
            
            # Collection for session notes/observations
            self.sessions = self.client.get_or_create_collection(
                name="session_notes",
                metadata={"hnsw:space": "cosine"}
            )
            
            # Collection for therapy strategies that worked
            self.strategies = self.client.get_or_create_collection(
                name="effective_strategies"
            )
            logger.info(f"Initialized ChromaDB at {settings.db.VECTOR_DB_PATH}")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            self.client = None

    def store_session_note(self, child_id: int, session_id: int, note: str, metadata: Dict[str, Any] = None):
        if not self.client: return
        
        meta = {"child_id": child_id, "session_id": session_id, "timestamp": str(datetime.now())}
        if metadata:
            meta.update(metadata)
            
        doc_id = f"child_{child_id}_session_{session_id}_{hash(note)}"
        
        try:
            self.sessions.add(
                documents=[note],
                metadatas=[meta],
                ids=[doc_id]
            )
            logger.debug(f"Stored session note in vector db for child {child_id}")
        except Exception as e:
            logger.error(f"Error storing vector memory: {e}")

    def find_similar_situations(self, query: str, child_id: int = None, n: int = 3) -> List[str]:
        """Find past session notes similar to the current situation."""
        if not self.client: return []
        
        try:
            where_filter = {"child_id": child_id} if child_id else None
            
            results = self.sessions.query(
                query_texts=[query],
                n_results=n,
                where=where_filter
            )
            
            if results and results['documents'] and results['documents'][0]:
                return results['documents'][0]
            return []
        except Exception as e:
            logger.error(f"Error querying vector memory: {e}")
            return []

    def store_strategy(self, child_id: int, strategy_desc: str, success_score: float):
        if not self.client: return
        
        doc_id = f"child_{child_id}_strat_{hash(strategy_desc)}"
        try:
            self.strategies.add(
                documents=[strategy_desc],
                metadatas=[{"child_id": child_id, "success_score": success_score}],
                ids=[doc_id]
            )
        except Exception as e:
            logger.error(f"Error storing strategy: {e}")

    def get_relevant_strategies(self, situation_description: str, child_id: int = None, n: int = 2) -> List[str]:
        if not self.client: return []
        
        try:
            where_filter = {"child_id": child_id} if child_id else None
            results = self.strategies.query(
                query_texts=[situation_description],
                n_results=n,
                where=where_filter
            )
            
            if results and results['documents'] and results['documents'][0]:
                return results['documents'][0]
            return []
        except Exception as e:
            logger.error(f"Error querying strategies: {e}")
            return []
