"""
Supabase Database Client
Replaces SQLite connections with Supabase PostgreSQL
"""

import os
from typing import Optional
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()


class SupabaseClient:
    """Singleton Supabase client for whale alert service"""

    _instance: Optional[Client] = None

    @classmethod
    def get_client(cls) -> Client:
        """Get or create Supabase client instance"""
        if cls._instance is None:
            url = os.getenv('SUPABASE_URL')
            key = os.getenv('SUPABASE_KEY')  # Use publishable key for normal operations

            if not url or not key:
                raise ValueError(
                    "SUPABASE_URL and SUPABASE_KEY must be set in .env file"
                )

            cls._instance = create_client(url, key)

        return cls._instance

    @classmethod
    def reset(cls):
        """Reset the client (useful for testing)"""
        cls._instance = None


# Convenience function
def get_supabase() -> Client:
    """Get Supabase client instance"""
    return SupabaseClient.get_client()
