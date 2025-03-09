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


load_dotenv()
client = Groq(api_key=os.getenv("GROQ_CLOUD_API_KEY"))


class Extract:
    def get_transcription(self,filename) ->str:
        with open(filename, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(filename, file.read()),
                model="whisper-large-v3-turbo",
                response_format="verbose_json",
            )
            return transcription.text
            # print(transcription.text)

    def normalize_date_time(self, date_str, time_str):
        """
        Normalize dates and times into absolute datetime objects.
        Return the date in YYYY-MM-DD format and the time in 12-hour AM/PM format.
        """
        # Parse the date string (e.g., "today", "tomorrow", "next Monday", "2024-05-20")
        date = dateparser.parse(date_str)

        # Parse the time string (e.g., "9 PM", "15:00")
        time = dateparser.parse(time_str)

        # If the date is relative (e.g., "today", "tomorrow"), calculate it dynamically
        if date:
            # Get the current date and time
            now = datetime.now()

            # Calculate the difference between the parsed date and the current date
            delta = date - now

            # If the date is in the future (e.g., "tomorrow"), adjust it
            if delta.days >= 0:
                date = now + timedelta(days=delta.days)
            else:
                # Handle cases like "last Monday" (not common in your use case)
                date = now + timedelta(days=delta.days)

        # Combine date and time into a single datetime object
        if date and time:
            normalized_datetime = datetime(
                date.year, date.month, date.day,
                time.hour, time.minute, time.second
            )
            # Format the date as YYYY-MM-DD
            formatted_date = normalized_datetime.strftime("%Y-%m-%d")
            # Format the time as 12-hour AM/PM (e.g., 08:00:PM)
            formatted_time = normalized_datetime.strftime("%I:%M:%p")
            return formatted_date, formatted_time
        else:
            return None, None
    
    
    def GetInfofromtext(self,text) -> str:
        prompt = f"""
        Extract the following key information from the text below and provide JSON output:
        
        1. Subject of the meeting
        2. Date of the meeting (e.g., "today", "tomorrow", "next Monday", "2024-05-20")
        3. Time of the meeting (e.g., "9 PM", "15:00")
        4. Participants (extract EMAIL ADDRESSES of people involved or set Email which look real by using their name)
        5. Summary of the conversation (key points discussed)
        
        Format the output as a JSON object with keys:
        - subject
        - Date 
        - time of the meeting
        - participants (list of emails)
        - summary

        Text:
        {text}
        """
        
        llm = client.chat.completions.create(
            model="deepseek-r1-distill-llama-70b",
            messages=[
                {"role": "system", "content": """You are a helpful assistant that extracts key information from meeting transcripts.
                 """},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5, 
            max_tokens=1024,
            top_p=1,
            stream=False, 
            stop=None,
        )

        response = llm.choices[0].message.content
        def clean_response(response):
            if "<think>" in response:
                response = response.split("</think>")[-1].strip()
            return response
        res = clean_response(response)
        res = res.replace("```json\n","").replace("```","")
        
        res = json.loads(res)
        # print(type(res))
        date, time = self.normalize_date_time(res.get("Date"), res.get("time of the meeting"))
        if date and time:
            res["Date"] = date
            res["time of the meeting"] = time
        else:
            return {"error": "Failed to normalize date and time."}
        return res

# if not os.path.exists("client_secret_210713487910-k6rde3fjms01elsentbi3q2v9bonb6gj.apps.googleusercontent.com.json"):
#     print("Error: 'credentials.json' file not found. Please download it from the Google Cloud Console.")
# else:
#     print("'credentials.json' file found.")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

class AddTo_calander:
    def authenticate_google_calendar(self):
        creds = None
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        
        # If no valid credentials, prompt the user to log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Save the credentials for the next run
            with open("token.json", "w") as token:
                token.write(creds.to_json())
        
        return creds

    def check_if_meeting_exists(self, service, date_time):
        
        print(f"it form ythe cheak if meeting exists {date_time}")
        try:
            event_time = datetime.strptime(date_time, "%Y-%m-%dT%I:%M:%p")
            
            local_tz = pytz.timezone("Asia/Kolkata")
            event_time = local_tz.localize(event_time)
            
            # Format start and end times (assume 1-hour meeting)
            start_time = event_time.isoformat()
            end_time = (event_time + timedelta(hours=1)).isoformat()

            # Fetch events within the time range
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
            if len(events) < 0: print("Slot available to to set meeting .")
            return len(events) > 0

        except ValueError as e:
            print(f"Error parsing date_time: {e}")
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
            print(f"Event created: {event.get('htmlLink')}")
            return True

        except ValueError as e:
            print(f"Invalid date/time format: {e}")
            return False
        except Exception as e:
            print(f"An error occurred: {e}")
            return False

    def handle_meeting(self,subject, date_time, participants, summary):
        
        creds = self.authenticate_google_calendar()
        service = build("calendar", "v3", credentials=creds)

        if self.check_if_meeting_exists(service, date_time):
            print("A meeting already exists at this date and time.")
            return False

        return self.add_meeting_to_calendar(service, subject, date_time, participants, summary)

      
      
x = Extract()

transcribed_text = x.get_transcription("20250308_125404.mp4")
keyInfo_about_meet = x.GetInfofromtext(transcribed_text)
print(type(keyInfo_about_meet))
print(keyInfo_about_meet)

subject = keyInfo_about_meet.get("subject")
date_time = f"{keyInfo_about_meet.get('Date')}T{keyInfo_about_meet.get('time of the meeting')}"
participants = keyInfo_about_meet.get("participants")
summary = keyInfo_about_meet.get("summary")

x2 = AddTo_calander()
result = x2.handle_meeting(subject, date_time, participants, summary)
print(result)