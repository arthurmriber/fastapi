from fastapi import FastAPI, Request
from routers import getnews
from routers import filter
from routers import inference
from routers import analyze
from routers import search
from routers import searchterm
from routers import inference_createposter
from routers import cronjob

# Instancia a aplicação FastAPI
app = FastAPI()
    
@app.get("/")
def greet_json():
    return {"Hello": "World!"}

# Inclui as rotas
app.include_router(getnews.router)
app.include_router(filter.router)
app.include_router(inference.router)
app.include_router(analyze.router)
app.include_router(search.router)
app.include_router(searchterm.router)
app.include_router(inference_createposter.router)
app.include_router(cronjob.router)
