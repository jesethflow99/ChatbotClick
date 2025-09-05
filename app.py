from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Text, DateTime, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from dotenv import load_dotenv
from google import genai
from datetime import datetime
import os
import asyncio
from tenacity import retry, wait_fixed, stop_after_attempt
from google.genai.errors import ServerError

# -----------------------
# Configuración FastAPI
# -----------------------
app = FastAPI()
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

# -----------------------
# CORS
# -----------------------
origins = [
    "http://localhost",
    "http://localhost:5174/chatbot.github.io/",
    os.getenv("FRONTEND_URL", "http://localhost:5174")
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------
# SQLAlchemy
# -----------------------
Base = declarative_base()
DB_URL = "sqlite:///chat_history.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Interaction(Base):
    __tablename__ = "interactions"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    session_id = Column(String, index=True)
    personaje = Column(String)
    user_message = Column(Text)
    assistant_reply = Column(Text)
    tokens_used = Column(Integer)

Base.metadata.create_all(bind=engine)

# Dependency para sesiones DB
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -----------------------
# Modelo de request
# -----------------------
class ChatRequest(BaseModel):
    session_id: str
    message: str
    personaje: str = "profesor"
    description: str = ""  # nueva

# -----------------------
# Historial en RAM
# -----------------------
conversations = {}

# -----------------------
# System prompt
# -----------------------
SYSTEM_PROMPT = """..."""  # manten tu prompt original

# -----------------------
# Función async para llamar a Gemini con retry
# -----------------------
@retry(wait=wait_fixed(2), stop=stop_after_attempt(3), retry=(lambda e: isinstance(e, ServerError)))
async def call_genai_async(contents):
    loop = asyncio.get_event_loop()
    # Ejecuta la llamada síncrona de Gemini en un thread para no bloquear
    response = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(
            model="gemini-1.5-flash",
            contents=contents
        )
    )
    return response

# -----------------------
# Endpoint Chat
# -----------------------
@app.post("/chat")
async def chat_endpoint(request: ChatRequest, db: Session = Depends(get_db)):
    if not request.session_id.strip() or not request.message.strip():
        return {"error": "session_id y message son obligatorios"}

    # Cargar historial en RAM
    history = conversations.get(request.session_id, [])

    # Construir contenido para Gemini
    contents = [SYSTEM_PROMPT.format(
        personaje=request.personaje,
        description=request.description
    )]
    for msg in history:
        contents.append(f"{msg['role']}: {msg['content']}")
    contents.append(f"Usuario: {request.message}\nAsistente:")

    # Llamada a Gemini con manejo de errores
    try:
        response = await call_genai_async(contents)
        text = response.text
        tokens = response.usage_metadata.total_token_count
    except ServerError:
        text = "El modelo está saturado 😅, intenta de nuevo en unos segundos."
        tokens = 0

    # Guardar en RAM
    history.append({"role": "user", "content": request.message})
    history.append({"role": "assistant", "content": text})
    conversations[request.session_id] = history

    # Guardar en SQLite solo si hubo éxito
    if tokens > 0:
        interaction = Interaction(
            session_id=request.session_id,
            personaje=request.personaje,
            user_message=request.message,
            assistant_reply=text,
            tokens_used=tokens
        )
        db.add(interaction)
        db.commit()

    return {
        "reply": text,
        "tokens_used": tokens,
        "history_len": len(history)
    }
