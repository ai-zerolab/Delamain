import click
import uvicorn

from delamain.app import app


@click.command()
@click.option("--host", type=click.STRING, default="localhost")
@click.option("--port", type=click.INT, default=9870)
def start(host, port):
    """
    Start the server.
    """
    uvicorn.run(app, host=host, port=port)


@click.group()
def cli():
    pass


cli.add_command(start)
