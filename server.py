#!/usr/bin/env python3
"""
Simple MCP stdio server that provides basic text manipulation tools.
"""

import json
import asyncio
from mcp.server import Server
from mcp.types import Tool, TextContent
import os
from dotenv import load_dotenv
from starlette.routing import Route
import logging
from event_store import InMemoryEventStore
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
import uvicorn
from starlette.middleware.cors import CORSMiddleware
import contextlib

# Create server instance
server = Server("simple-text-server")

load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="reverse_text",
            description="Reverses the input text",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to reverse"
                    }
                },
                "required": ["text"]
            }
        ),
        Tool(
            name="count_words",
            description="Counts the number of words in the input text",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to count words in"
                    }
                },
                "required": ["text"]
            }
        ),
        Tool(
            name="uppercase_text",
            description="Converts text to uppercase",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to convert to uppercase"
                    }
                },
                "required": ["text"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    if name == "reverse_text":
        text = arguments.get("text", "")
        reversed_text = text[::-1]
        return [TextContent(type="text", text=f"Reversed: {reversed_text}")]
    
    elif name == "count_words":
        text = arguments.get("text", "")
        word_count = len(text.split())
        return [TextContent(type="text", text=f"Word count: {word_count}")]
    
    elif name == "uppercase_text":
        text = arguments.get("text", "")
        upper_text = text.upper()
        return [TextContent(type="text", text=f"Uppercase: {upper_text}")]
    
    else:
        raise ValueError(f"Unknown tool: {name}")

async def create_app():

    # Create an event store for resumability
    event_store = InMemoryEventStore(max_events_per_stream=100)

    # Create the session manager with the event store
    try:
        # Try with auth parameters (newer MCP versions)
        session_manager = StreamableHTTPSessionManager(
            app=server,
            event_store=event_store,  # Use our event store for resumability
            json_response=False,  # Use SSE format for responses
            stateless=False,  # Stateful mode for better user experience
        )
        logging.info(
            "StreamableHTTPSessionManager initialized with authentication support"
        )
    except TypeError:
        # Fallback for older MCP versions that don't support auth
        logging.warning(
            "Your MCP version doesn't support authentication in StreamableHTTPSessionManager"
        )
        logging.warning(
            "Initializing StreamableHTTPSessionManager without authentication"
        )

        # Try with just the basic parameters
        try:
            session_manager = StreamableHTTPSessionManager(
                app=server,
                event_store=event_store,
                json_response=False,
            )
            logging.info(
                "StreamableHTTPSessionManager initialized without authentication"
            )
        except TypeError:
            # If that still fails, try with minimal parameters
            logging.warning(
                "Falling back to minimal StreamableHTTPSessionManager initialization"
            )
            session_manager = StreamableHTTPSessionManager(app=server)
    except Exception as e:
        logging.error(f"Failed to initialize StreamableHTTPSessionManager: {e}")
        session_manager = None


    # Create a class for handling streamable HTTP connections
    class HandleStreamableHttp:
        def __init__(self, session_manager):
            self.session_manager = session_manager

        async def __call__(self, scope, receive, send):
            if self.session_manager is not None:
                try:
                    logging.info("Handling Streamable HTTP connection ....")
                    await self.session_manager.handle_request(scope, receive, send)
                    logging.info("Streamable HTTP connection closed ....")
                except Exception as e:
                    logging.error(f"Error handling Streamable HTTP request: {e}")
                    await send({
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [(b"content-type", b"application/json")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": json.dumps({
                            "error": f"Internal server error: {str(e)}"
                        }).encode("utf-8"),
                    })
            else:
                # Return a 501 Not Implemented response if streamable HTTP is not available
                await send(
                    {
                        "type": "http.response.start",
                        "status": 501,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": json.dumps(
                            {"error": "Streamable HTTP transport is not available"}
                        ).encode("utf-8"),
                    }
                )

    
    # Helper functions for OAuth handlers
    async def get_request_body(receive):
        """Get request body from ASGI receive function."""
        body = b""
        more_body = True

        while more_body:
            message = await receive()
            body += message.get("body", b"")
            more_body = message.get("more_body", False)

        return body.decode("utf-8")

    async def send_json_response(send, status, data):
        """Send JSON response."""
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": json.dumps(data).encode("utf-8"),
            }
        )

    # Define routes
    routes = []

    # Add Streamable HTTP route if available
    if session_manager is not None:
        routes.append(
            Route(
                "/mcp", endpoint=HandleStreamableHttp(session_manager), methods=["POST"]
            )
        )

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]

    # Define lifespan for session manager
    @contextlib.asynccontextmanager
    async def lifespan(app):
        """Context manager for session manager."""
        if session_manager is not None:
            async with session_manager.run():
                logging.info("Application started with StreamableHTTP session manager!")
                try:
                    yield
                finally:
                    logging.info("Application shutting down...")
        else:
            # No session manager, just yield
            yield

    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


async def start_server():
    """Start the server asynchronously."""
    app = await create_app()
    logging.info(f"Starting server at {HOST}:{PORT}")

    # Use uvicorn's async API
    config = uvicorn.Config(app, host=HOST, port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
     while True:
        try:
            # Use asyncio.run to run the async start_server function
            asyncio.run(start_server())
        except KeyboardInterrupt:
            logging.info("Server stopped by user")
            break
        except Exception as e:
            logging.error(f"Server crashed with error: {e}")
            continue

