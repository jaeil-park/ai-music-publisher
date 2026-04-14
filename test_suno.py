import logging  
logging.basicConfig(level=logging.DEBUG)  
from src.media_generator import generate_and_download_audio  
concept={'title':'Test', 'genre':'pop', 'lyrics':'test', 'audio_prompt':'pop'}  
generate_and_download_audio(concept)  
