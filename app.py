from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Text, DateTime, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from dotenv import load_dotenv
from google import genai
from datetime import datetime
import os

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
    "http://localhost:5173",
    "http://127.0.0.1:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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
SYSTEM_PROMPT = """
Eres un profesor de inglés para principiantes hispanohablantes.
Tu personaje es: {personaje}.
Descripción del personaje: {description}

📌 Instrucciones:

1. Comienza hablando principalmente en español (80-90%), pero introduce **una palabra o frase simple en inglés por mensaje**.
2. Explica el significado de la palabra en español y da un ejemplo corto.
3. Haz que el estudiante repita o use la palabra en una frase sencilla.
4. Mantén las respuestas **muy cortas y claras** para no saturar al estudiante.
5. Sé amable, paciente e interactivo, corrige errores suavemente.
6. Gradualmente puedes ir mezclando más inglés a medida que el estudiante lo entiende.
7. Siempre adapta los ejemplos al personaje y al tema que se está enseñando.

Ejemplo de interacción:

Usuario: dinosaurios  
Profesor: ¡Dinosaurios! En inglés se dice *dinosaurs*. Repite conmigo: "Dinosaurs".  
Profesor: Los *dinosaurs* vivieron en la *Mesozoic Era*. ¿Puedes decir "Dinosaurs live a long time"?  

Fin de instrucciones.
"""


# -----------------------
# Endpoint Chat
# -----------------------
@app.post("/chat")
async def chat_endpoint(request: ChatRequest, db: Session = Depends(get_db)):
    # Validación simple
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

    # Llamada al modelo
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=contents,
    )

    # Guardar en RAM
    history.append({"role": "user", "content": request.message})
    history.append({"role": "assistant", "content": response.text})
    conversations[request.session_id] = history

    # Guardar en SQLite
    interaction = Interaction(
        session_id=request.session_id,
        personaje=request.personaje,
        user_message=request.message,
        assistant_reply=response.text,
        tokens_used=response.usage_metadata.total_token_count
    )
    db.add(interaction)
    db.commit()

    return {
        "reply": response.text,
        "tokens_used": response.usage_metadata.total_token_count,
        "history_len": len(history)
    }

