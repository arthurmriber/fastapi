from fastapi import FastAPI

app = FastAPI()

@app.get("/hi")
def read_root():
    return {"message": "hello world"}
