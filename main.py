from fastapi import FastAPI, Request
from routers import getnews # Importa as rotas
from routers import filter
from routers import search

# Instancia a aplicação FastAPI
app = FastAPI()

@app.get("/")
def greet_json():
    return {"Hello": "World!"}

# Inclui as rotas
app.include_router(getnews.router)
app.include_router(filter.router)
app.include_router(search.router)
