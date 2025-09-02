from fastapi import FastAPI, Request
from routers import getnews # Importa as rotas

# Instancia a aplicação FastAPI
app = FastAPI()

@app.get("/")
def greet_json():
    return {"Hello": "World!"}

# Inclui as rotas
app.include_router(getnews.router)
