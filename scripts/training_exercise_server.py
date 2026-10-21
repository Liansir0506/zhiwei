"""Install training middleware while retaining all frozen business routes/guards."""
import uvicorn
from scripts import training_exercise_runtime as runtime
from qa_core.presentation.amf_training import install


def main():
    runtime.configure()
    original=uvicorn.run
    def run(app,**kwargs):
        install(app,runtime.base.core.state(),runtime.base)
        original(app,**kwargs)
    uvicorn.run=run
    from scripts.training_business_server import main as business
    business()


if __name__=='__main__':main()
