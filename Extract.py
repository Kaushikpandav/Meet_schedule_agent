import os
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import dateparser
import json
import pytz
import re
import streamlit as st
from pydub import AudioSegment
import time

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_CLOUD_API_KEY"))

class Extract:
    def split_audio(self, filename, chunk_length_ms=300000):  # 5 minutes
        try:
            audio = AudioSegment.from_file(filename)
            chunks = []
            st.write(f"Audio duration: {len(audio) / 60000:.1f} minutes")
            for i in range(0, len(audio), chunk_length_ms):
                chunk = audio[i:i + chunk_length_ms]
                chunk_name = f"chunk_{i // chunk_length_ms}_{filename}"
                chunk.export(chunk_name, format="mp3", bitrate="64k")
                chunk_size_mb = os.path.getsize(chunk_name) / (1024 * 1024)
                st.write(f"Created {chunk_name} ({chunk_size_mb:.1f}MB)")
                chunks.append(chunk_name)
            return chunks
        except Exception as e:
            st.error(f"Failed to split audio: {str(e)}")
            return []

    def get_transcription(self, filename, retries=3, delay=5):
        file_size_mb = os.path.getsize(filename) / (1024 * 1024)
        st.write(f"File size: {file_size_mb:.1f}MB")

        if file_size_mb > 10:
            st.write("File is large. Splitting into chunks...")
            chunk_files = self.split_audio(filename)
            if not chunk_files:
                return None
            full_transcript = ""
            for idx, chunk_file in enumerate(chunk_files):
                st.write(f"Processing chunk {idx + 1}/{len(chunk_files)}...")
                chunk_transcript = ""
                for attempt in range(retries):
                    try:
                        with open(chunk_file, "rb") as file:
                            transcription = client.audio.transcriptions.create(
                                file=(chunk_file, file.read()),
                                model="whisper-large-v3-turbo",
                                response_format="verbose_json",
                            )
                            chunk_transcript = transcription.text
                            break
                    except Exception as e:
                        error_str = str(e)
                        if "502" in error_str or "520" in error_str:
                            st.warning(f"Chunk {idx + 1}, Attempt {attempt + 1}/{retries}: Groq API error ({'502' if '502' in error_str else '520'}). Retrying in {delay}s...")
                        else:
                            st.warning(f"Chunk {idx + 1}, Attempt {attempt + 1}/{retries}: {error_str}. Retrying...")
                        if attempt == retries - 1:
                            st.error(f"Failed to transcribe chunk {idx + 1} after {retries} attempts.")
                            chunk_transcript = "[Transcription failed for this segment]"
                        time.sleep(delay)
                full_transcript += chunk_transcript + " "
                if os.path.exists(chunk_file):
                    os.remove(chunk_file)
            return full_transcript.strip() if full_transcript else None
        else:
            for attempt in range(retries):
                try:
                    with open(filename, "rb") as file:
                        transcription = client.audio.transcriptions.create(
                            file=(filename, file.read()),
                            model="whisper-large-v3-turbo",
                            response_format="verbose_json",
                        )
                        return transcription.text
                except Exception as e:
                    error_str = str(e)
                    if "502" in error_str or "520" in error_str:
                        st.warning(f"Attempt {attempt + 1}/{retries}: Groq API error ({'502' if '502' in error_str else '520'}). Retrying in {delay}s...")
                    else:
                        st.warning(f"Attempt {attempt + 1}/{retries}: {error_str}. Retrying...")
                    if attempt == retries - 1:
                        st.error("All attempts failed to transcribe small file.")
                        return None
                    time.sleep(delay)

    def normalize_date_time(self, date_str, time_str):
        now = datetime.now()
        date = dateparser.parse(date_str, settings={'RELATIVE_BASE': now})
        time_obj = dateparser.parse(time_str)
        if date and time_obj:
            normalized_datetime = datetime(
                date.year, date.month, date.day,
                time_obj.hour, time_obj.minute, time_obj.second
            )
            formatted_date = normalized_datetime.strftime("%Y-%m-%d")
            formatted_time = normalized_datetime.strftime("%I:%M:%p")
            return formatted_date, formatted_time
        return None, None

    def GetInfofromtext(self, text):
        if not text:
            st.error("No transcription available. Cannot extract meeting info.")
            return {"error": "No transcription"}
        if "[Transcription failed for this segment]" in text and len(text.strip()) <= len("[Transcription failed for this segment]"):
            st.error("Transcription completely failed. Cannot extract meeting info.")
            return {"error": "Transcription failed"}

        prompt = f"""
        From the following text, extract key meeting information and return it as a JSON object. The text may contain a mix of Hindi and English. Focus on identifying:
        1. Subject of the meeting (e.g., project discussion, demo)
        2. Date of the meeting (e.g., "tomorrow", "next Monday", "13th March")
        3. Time of the meeting (e.g., "4 baje", "15:00")
        4. Participants (generate realistic email addresses if names are mentioned, e.g., "Kausik" -> "kausik@example.com", or use "user@example.com" if none found)
        5. Summary of key points discussed
        
        Return the output in this JSON format:
        {{
          "subject": "<subject>",
          "Date": "<date>",
          "time of the meeting": "<time>",
          "participants": ["email1@example.com", "email2@example.com"],
          "summary": "<summary>"
        }}
        If any field cannot be determined, use reasonable defaults or leave as empty string/list.

        Text:
        {text}
        """
        for attempt in range(3):
            try:
                llm = client.chat.completions.create(
                    model="deepseek-r1-distill-llama-70b",
                    messages=[
                        {"role": "system", "content": "You are an assistant that extracts meeting info from mixed Hindi-English transcripts. Return valid JSON only, no extra text."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.5,
                    max_tokens=1024,
                    top_p=1,
                    stream=False,
                    stop=None,
                )
                response = llm.choices[0].message.content
                st.write(f"Debug: Raw LLM response: {response}")
                # Extract JSON using regex to handle extra text
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    cleaned_response = json_match.group(0)
                else:
                    raise ValueError("No JSON found in response")
                res = json.loads(cleaned_response)
                date, normalized_time = self.normalize_date_time(res.get("Date", ""), res.get("time of the meeting", ""))
                if date and normalized_time:
                    res["Date"] = date
                    res["time of the meeting"] = normalized_time
                else:
                    st.warning("Could not normalize date/time. Using raw values from LLM.")
                return res
            except Exception as e:
                st.warning(f"Attempt {attempt + 1}/3: Failed to extract info: {str(e)}. Retrying...")
                if attempt == 2:
                    st.error("All attempts failed to extract info from LLM.")
                    return {
                        "subject": "Unknown Meeting",
                        "Date": datetime.now().strftime("%Y-%m-%d"),
                        "time of the meeting": "12:00:PM",
                        "participants": ["user@example.com"],
                        "summary": "Failed to extract details from transcript."
                    }
                time.sleep(2)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

class AddTo_calander:
    def authenticate_google_calendar(self):
        creds = None
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
            with open("token.json", "w") as token:
                token.write(creds.to_json())
        return creds

    def check_if_meeting_exists(self, service, date_time):
        try:
            event_time = datetime.strptime(date_time, "%Y-%m-%dT%I:%M:%p")
            local_tz = pytz.timezone("Asia/Kolkata")
            event_time = local_tz.localize(event_time)
            start_time = event_time.isoformat()
            end_time = (event_time + timedelta(hours=1)).isoformat()
            events_result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=start_time,
                    timeMax=end_time,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = events_result.get("items", [])
            return len(events) > 0
        except ValueError as e:
            st.error(f"Error parsing date_time: {e}")
            return False

    def add_meeting_to_calendar(self, service, subject, date_time, participants, summary):
        try:
            event_time = datetime.strptime(date_time, "%Y-%m-%dT%I:%M:%p")
            local_tz = pytz.timezone("Asia/Kolkata")
            event_time = local_tz.localize(event_time)
            start_time = event_time.isoformat()
            end_time = (event_time + timedelta(hours=1)).isoformat()
            description = f"Participants: {', '.join(participants)}\nSummary: {summary}"
            event = {
                "summary": subject,
                "description": description,
                "start": {"dateTime": start_time, "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": end_time, "timeZone": "Asia/Kolkata"},
                "reminders": {"useDefault": True},
            }
            event = service.events().insert(calendarId="primary", body=event).execute()
            return True, event.get('htmlLink')
        except ValueError as e:
            st.error(f"Invalid date/time format: {e}")
            return False, None
        except HttpError as e:
            st.error(f"Google Calendar error: {e}")
            return False, None

    def handle_meeting(self, subject, date_time, participants, summary):
        creds = self.authenticate_google_calendar()
        service = build("calendar", "v3", credentials=creds)
        if self.check_if_meeting_exists(service, date_time):
            st.warning("A meeting already exists at this date and time.")
            return False, None
        return self.add_meeting_to_calendar(service, subject, date_time, participants, summary)

# Streamlit App
st.title("Meeting Scheduler from Audio")
st.write("Upload an audio file to extract meeting details and schedule it on Google Calendar.")

uploaded_file = st.file_uploader("Choose an audio file", type=["mp3", "mp4", "wav", "m4a"])

if uploaded_file is not None:
    with open(uploaded_file.name, "wb") as f:
        f.write(uploaded_file.read())
    audio_path = uploaded_file.name

    if st.button("Process Audio and Schedule Meeting"):
        with st.spinner("Processing audio..."):
            try:
                x = Extract()
                transcribed_text = x.get_transcription(audio_path, retries=3, delay=5)
                if not transcribed_text:
                    st.error("Transcription failed completely.")
                else:
                    st.write("### Transcribed Text")
                    st.text(transcribed_text)
                    key_info = x.GetInfofromtext(transcribed_text)
                    if "error" not in key_info:
                        st.write("### Extracted Meeting Info")
                        st.json(key_info)
                        subject = key_info.get("subject", "Unknown Meeting")
                        date = key_info.get("Date", datetime.now().strftime("%Y-%m-%d"))
                        meeting_time = key_info.get("time of the meeting", "12:00:PM")
                        date_time = f"{date}T{meeting_time}"
                        participants = key_info.get("participants", ["user@example.com"])
                        summary = key_info.get("summary", "No summary available")
                        st.write("### Scheduling Meeting...")
                        x2 = AddTo_calander()
                        success, link = x2.handle_meeting(subject, date_time, participants, summary)
                        if success:
                            st.success(f"Meeting scheduled successfully! [View on Google Calendar]({link})")
                        else:
                            st.error("Failed to schedule the meeting.")
                    else:
                        st.error("Failed to extract meeting info.")
            except Exception as e:
                st.error(f"An error occurred: {str(e)}")
            finally:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
else:
    st.info("Please upload an audio file to proceed.")
