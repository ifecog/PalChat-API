import os
import logging

from datetime import datetime
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from sqlalchemy.orm import Session

from app.models.chat import ChatSession, ChatMessage
from app.core.vector_engine import retrieve_similar_chunks


load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant. Respond concisely and clearly."


class ChatEngine:
    def __init__(self):
        self.llm = ChatOpenAI(
            model='gpt-4o',
            temperature=0.7,
            api_key=os.getenv('OPENAI_API_KEY')
        )
        logger.info("Chat engine initialized with GPT model.")
        
    def _get_conversation_messages(self, session_id: str, db: Session):
        db_messages = db.query(ChatMessage).filter(
            ChatMessage.session_id == session_id
        ).order_by(ChatMessage.timestamp).all()
        
        langchain_messages = []
        for msg in db_messages:
            if msg.message_type == 'human':
                langchain_messages.append(HumanMessage(content=msg.content))
            elif msg.message_type == 'ai':
                langchain_messages.append(AIMessage(content=msg.content))
                
        return langchain_messages
        
    def _save_ai_messages(self, session_id: str, ai_message: str, db: Session):
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        if not session:
            session = ChatSession(session_id=session_id)
            db.add(session)
        else:
            session.updated_at = datetime.utcnow()
        
        ai_msg = ChatMessage(
            session_id=session_id,
            message_type='ai',
            content=ai_message
        )
        
        db.add(ai_msg) 
        db.commit()
        logger.info(f"Messages saved to db for session: {session_id}")
        
    def _build_prompt_with_rag_context(self, query: str):
        chunks = retrieve_similar_chunks(query, k=4)
        
        if not chunks:
            return query
        
        context = '\n--\n'.join([doc.page_content for doc in chunks])
        prompt = (
            f"You are an helpful assistant.\n"
            f"Use the context below to answer the user's question.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {query}\nAnswer:"
        )
        
        return prompt
        
    async def get_response(self, message: str, session_id: str, db: Session):       
        try:
            logger.info(f"Recieved message for {session_id}: {message}")
            
            conversation_messages = self._get_conversation_messages(session_id, db)
            
            prompt_with_context = self._build_prompt_with_rag_context(message)
            
            current_messages = conversation_messages + [HumanMessage(content=prompt_with_context)]
            
            ai_response = self.llm.invoke(current_messages)
            logger.info(f"LLM Response: {ai_response.content}")
            
            self._save_ai_messages(session_id, ai_response.content, db)
            
            return ai_response.content
        
        except Exception as e:
            logger.exception("Error in get_response")
            raise Exception(f'Error getting AI response: {str(e)}')
        
        finally:
            db.close()
        
    def clear_session(self, session_id: str, db: Session):
        try:
            messages_deleted = db.query(ChatMessage).filter(
                ChatMessage.session_id == session_id
            ).delete()
            
            session_deleted = db.query(ChatSession).filter(
                ChatSession.session_id == session_id
            ).delete()
            
            db.commit()
            logger.info(f"Session {session_id} cleared. Messages: {messages_deleted}, Session: {session_deleted}")
            return messages_deleted > 0 or session_deleted > 0
        
        except Exception as e:
            db.rollback()
            logger.exception("Error clearing session.")
            raise Exception(f"Error clearing session: {str(e)}")

    def get_session_history(self, session_id: str, db: Session):
        messages = db.query(ChatMessage).filter(
            ChatMessage.session_id == session_id
        ).order_by(ChatMessage.timestamp).all()
            
        if not messages:
            return []
            
        history = []
        for msg in messages:
            history.append({
                "timestamp": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "sender": "Human" if msg.message_type == "human" else "AI",
                "message": msg.content
            })
            
        return history    
        
chat_engine = ChatEngine()

