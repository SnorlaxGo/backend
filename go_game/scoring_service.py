from typing import Dict, Optional, Set, Tuple
from datetime import datetime
import json
from .schemas import ScoringStatus, ScoringNotification, Coordinate
from .logging_config import logger
from .event_manager import redis_manager
class ScoringService:
    """Handles game scoring coordination via Redis"""
    
    def __init__(self, redis_manager):
        self.redis = redis_manager
        self.SCORING_TTL = 300  # 5 minutes
    
    def _get_scoring_key(self, game_id: int, player_id: int) -> str:
        """Get Redis key for player scoring data"""
        return f"scoring:{game_id}:{player_id}"
    
    def _get_agreement_key(self, game_id: int) -> str:
        """Get Redis key for scoring agreement status"""
        return f"scoring_agreed:{game_id}"
    
    async def submit_scoring(self, game_id: int, player_id: int, 
                           white_territory: list[Coordinate], 
                           black_territory: list[Coordinate],
                           black_score: float, white_score: float) -> None:
        """Store a player's scoring submission"""
        scoring_data = {
            "white_territory": [{"x": coord.x, "y": coord.y} for coord in white_territory],
            "black_territory": [{"x": coord.x, "y": coord.y} for coord in black_territory],
            "black_score": black_score,
            "white_score": white_score,
            "submitted_at": datetime.utcnow().isoformat()
        }
        
        key = self._get_scoring_key(game_id, player_id)
        
        try:
            await self.redis.set(key, scoring_data, ex=self.SCORING_TTL)
            logger.debug(f"Stored scoring data for game {game_id}, player {player_id}")
        except Exception as e:
            logger.error(f"Failed to store scoring data: {str(e)}", exc_info=True)
            raise
    
    async def get_scoring_submissions(self, game_id: int) -> Dict[int, dict]:
        """Get all scoring submissions for a game"""
        pattern = f"scoring:{game_id}:*"
        
        try:
            keys = await self.redis.keys(pattern)
            submissions = {}
            
            for key in keys:
                player_id = int(key.split(':')[-1])
                data = await self.redis.get(key)
                if data:
                    submissions[player_id] = data
            
            return submissions
        except Exception as e:
            logger.error(f"Failed to get scoring submissions: {str(e)}", exc_info=True)
            return {}
    
    async def check_scoring_agreement(self, game_id: int, expected_players: Set[int]) -> Tuple[bool, bool, Optional[float], Optional[float]]:
        """
        Check if both players have submitted and if scores match
        Returns: (both_submitted, scores_match, black_score, white_score)
        """
        submissions = await self.get_scoring_submissions(game_id)
        submitted_players = set(submissions.keys())
        
        both_submitted = submitted_players == expected_players
        
        if not both_submitted:
            return False, False, None, None
        
        # Check if scores match
        scores = list(submissions.values())
        scores_match = (
            scores[0]["black_score"] == scores[1]["black_score"] and
            scores[0]["white_score"] == scores[1]["white_score"]
        )
        
        black_score = scores[0]["black_score"] if scores_match else None
        white_score = scores[0]["white_score"] if scores_match else None
        
        return both_submitted, scores_match, black_score, white_score
    
    async def mark_scoring_agreed(self, game_id: int) -> None:
        """Mark that scoring has been agreed upon"""
        key = self._get_agreement_key(game_id)
        
        try:
            await self.redis.set(key, {"agreed": True}, ex=self.SCORING_TTL)
            logger.debug(f"Marked scoring as agreed for game {game_id}")
        except Exception as e:
            logger.error(f"Failed to mark scoring agreement: {str(e)}", exc_info=True)
            raise
    
    async def is_scoring_agreed(self, game_id: int) -> bool:
        """Check if scoring has been agreed upon"""
        key = self._get_agreement_key(game_id)
        
        try:
            data = await self.redis.get(key)
            return data.get("agreed", False) if data else False
        except Exception as e:
            logger.error(f"Failed to check scoring agreement: {str(e)}", exc_info=True)
            return False
    
    async def clear_scoring_data(self, game_id: int) -> None:
        """Clear all scoring data for a game"""
        pattern = f"scoring:{game_id}:*"
        agreement_key = self._get_agreement_key(game_id)
        
        try:
            keys = await self.redis.keys(pattern)
            if keys:
                keys.append(agreement_key)
                await self.redis.delete(*keys)
                logger.debug(f"Cleared scoring data for game {game_id}")
        except Exception as e:
            logger.error(f"Failed to clear scoring data: {str(e)}", exc_info=True)
    
    async def get_scoring_status(self, game_id: int, expected_players: Set[int]) -> ScoringStatus:
        """Get the current scoring status for a game"""
        # Check if already agreed
        if await self.is_scoring_agreed(game_id):
            return ScoringStatus(
                players_submitted=list(expected_players),
                status="agreed",
                scores_match=True
            )
        
        submissions = await self.get_scoring_submissions(game_id)
        submitted_players = list(submissions.keys())
        
        if len(submitted_players) < len(expected_players):
            status = "waiting"
            scores_match = None
        else:
            both_submitted, scores_match, black_score, white_score = await self.check_scoring_agreement(
                game_id, expected_players
            )
            status = "agreed" if scores_match else "mismatch"
        
        return ScoringStatus(
            players_submitted=submitted_players,
            status=status,
            scores_match=scores_match
        )

# Create singleton instance
scoring_service = ScoringService(redis_manager) 