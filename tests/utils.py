import asyncio
from go_game.websocket_manager import manager, challenge_manager, redis_manager

async def reset_websocket_manager():
    """Reset the manager state for testing"""
    manager.active_connections = {}
    manager.player_game_connections = {}
    
    # Cancel any pending disconnect tasks
    for task in manager.disconnect_tasks.values():
        if not task.done():
            task.cancel()
    manager.disconnect_tasks = {}
    
    challenge_manager.active_connections = {}
    
    # Reset Redis connection
    if redis_manager.redis_conn:
        await redis_manager.disconnect()
    redis_manager.redis_conn = None
    redis_manager.pubsub = None
    redis_manager.listener_task = None 