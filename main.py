from fastapi import FastAPI, Request
from routers import profanity # Importa as rotas

# Instancia a aplicação FastAPI
app = FastAPI()

@app.get("/")
def greet_json():
    return {"Hello": "World!"}

# Inclui as rotas
app.include_router(profanity.router)
