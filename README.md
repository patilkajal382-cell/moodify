\# Moodify



A backend-only, mood-based music companion built on the Spotify Web API.

Describe how you feel in plain text and Moodify suggests songs, plays them

on your Spotify device, saves them as a playlist, and tracks your listening

habits, including whether you are a binge listener.



\## Features

\- Spotify login (OAuth Authorization Code flow) with automatic token refresh

\- Mood to music: an LLM (Groq) turns text like "tired, want calm music to focus"

&#x20; into a mood profile (moods, energy level, genres)

\- RAG-style retrieval: songs are embedded with sentence-transformers and stored

&#x20; in ChromaDB, then matched to the mood profile and filtered by energy

\- Personalised re-ranking: songs by your own top artists rank higher

\- Playback on your open Spotify device, and one-call playlist creation

\- Listening history logger (background job, SQLite) for weekly and monthly stats

\- Binge-listener detection from session length, artist concentration and repeats



\## Tech stack

Python, FastAPI, SQLite, ChromaDB, sentence-transformers, APScheduler,

Spotify Web API, Groq API



\## Setup

1\. Create an app at https://developer.spotify.com/dashboard and add the redirect

&#x20;  URI http://127.0.0.1:8000/callback (Spotify Premium is required)

2\. Copy .env.example to .env and fill in your keys

3\. pip install -r requirements.txt

4\. uvicorn main:app --reload

5\. 5. Open http://127.0.0.1:8000/login, then /build-catalogue once, then reload

&#x20;  /relabel-some until still\_to\_do is 0



\## Endpoints

/login, /top, /devices, /play, /sync, /stats?period=week|month,

/build-catalogue, /relabel-some, /mood, /suggest, /playlist



\## Known limitations

\- Spotify removed audio features and recommendations for new apps in 2024, so

&#x20; each song's mood and energy are labelled by an LLM from its title and artist,

&#x20; not by analysing the audio, so some labels can be off 

\- Weekly and monthly stats only cover the time since logging started, because

&#x20; Spotify returns just the last 50 plays

