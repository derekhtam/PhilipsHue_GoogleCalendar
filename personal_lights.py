from __future__ import print_function
from datetime import timedelta
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from qhue import Bridge
from qhue import QhueException

import datetime
import os.path
import pickle
import yaml
import random
import time
import atexit


# Allow printing to STDOUT
LOGGING = True

# You can override these in config.yaml
BRIDGE_IP = "1.1.1.1"  # Update with your Bridge's IP address
BRIDGE_USERNAME = "username"  # Update with your Bridge's username
LIGHTS = [1, 2]  # Update with your light's IDs
MULTICOLOR = True  # Set to false if you want one color ambient.

# How often to update the lights
LIGHT_CHANGE_INTERVAL_MIN = 4
LIGHT_CHANGE_INTERVAL_SEC = LIGHT_CHANGE_INTERVAL_MIN * 60

# How many minutes before the event to switch to GVC mode
# Should be larger than LIGHT_CHANGE_INTERVAL_MIN
CAL_EVENT_CHECK_INTERVAL_MIN = 5

with open("config.yaml") as config_file:
    config = yaml.load(config_file, Loader=yaml.FullLoader)
    if "BRIDGE_IP" in config:
        BRIDGE_IP = config["BRIDGE_IP"]
    if "BRIDGE_USERNAME" in config:
        BRIDGE_USERNAME = config["BRIDGE_USERNAME"]
    if "LIGHTS" in config:
        LIGHTS = [int(l) for l in config["LIGHTS"]]
    if "MULTICOLOR" in config:
        MULTICOLOR = config["MULTICOLOR"]
    if "GROUP" in config:
        GROUP = config["GROUP"]
    if "MEETING_SCENE_ID" in config:
        MEETING_SCENE_ID = config["MEETING_SCENE_ID"]
    if "WARM_TONES_SCENE_ID" in config:
        WARM_TONES_SCENE_ID = config["WARM_TONES_SCENE_ID"]

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def TurnOnLights(hueLights):
    try:
        [hueLights(light, "state") for light in LIGHTS]
    except QhueException as err:
        if LOGGING:
            print("Turning lights on")
        [hueLights(light, "state", on=True) for light in LIGHTS]


def TurnOffLights(hueLights):
    [hueLights(light, "state", on=False) for light in LIGHTS]


def SetAmbientColor(hueGroups, hueLights):
    # for setting lights agnostic of scene
    # x = round(random.random(), 3)
    # y = round(random.random(), 3)

    # [hueLights(light, 'state', xy=[x, y], bri=254, transitiontime=100)
    #  for light in LIGHTS]
    # if LOGGING:
    #     print('x={}, y={} @ {}'.format(x, y, datetime.datetime.now()))
    # return
    hueGroups(GROUP, "action", transitiontime=0, scene=WARM_TONES_SCENE_ID)


def SetAmbientMultiColor(hueLights):
    hue = int(random.random() * 65535)
    # complementary colors are opposites, but if you have n lights
    # this will distribute around the color wheel.
    inc = int(65535 / len(LIGHTS))

    for light in LIGHTS:
        if LOGGING:
            print("hue={}".format(hue))
        hueLights(light, "state", hue=hue, sat=254, bri=254, transitiontime=100)
        hue = (hue + inc) % 65535


def SetGVCColor(hueGroups, hueLights):
    # for setting lights agnostic of scene
    # red
    # hue = int(65535)
    # [hueLights(light, 'state', hue=hue, sat=254, bri=100)
    #  for light in LIGHTS]
    # return
    hueGroups(
        GROUP, "action", effect="colorloop", transitiontime=0, scene=MEETING_SCENE_ID
    )


def SetLightMode(hueGroups, hueLights, mode):
    if mode == "Ambient":
        if MULTICOLOR:
            SetAmbientMultiColor(hueLights)
        else:
            SetAmbientColor(hueGroups, hueLights)
    else:
        SetGVCColor(hueGroups, hueLights)


def GetCalendarEvents(service):
    now = datetime.datetime.utcnow().isoformat() + "Z"  # 'Z' indicates UTC time
    if LOGGING:
        print("Getting events...")
    try:
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except ConnectionResetError as err:
        print("Caught ConnectionResetError!")
        time.sleep(60)
        GetCalendarEvents(service)
        return

    events = events_result.get("items", [])

    if not events:
        if LOGGING:
            print("No upcoming events found.")

    for event in events:
        if "dateTime" in event["start"]:
            event_start_datetime_str = event["start"]["dateTime"]
            event_start_date_obj = datetime.datetime.strptime(
                event_start_datetime_str[:19], "%Y-%m-%dT%H:%M:%S"
            )
            event_end_datetime_str = event["end"]["dateTime"]
            event_end_date_obj = datetime.datetime.strptime(
                event_end_datetime_str[:19], "%Y-%m-%dT%H:%M:%S"
            )
            return EventNotify(event_start_date_obj, event_end_date_obj)
        else:
            if LOGGING:
                print("Whole day event: {}")

            continue
        # attendees = event.get('attendees')

        # # Only want events that have reminders set
        # if event['reminders']['useDefault']:
        #     # If I'm an attendee and I've accepted the invitation
        #     if attendees:
        #         for me in attendees:
        #             if me.get('self') and me.get('responseStatus') == 'accepted':
        #                 if LOGGING:
        #                     print('Attending event: {}'.format(
        #                         event['summary']))
        #                 return EventNotify(event_start_date_obj, event_end_date_obj)

        #     # If I'm the creator of the event and I've enabled it to remind me
        #     if event['creator'].get('self'):
        #         if LOGGING:
        #             print('Self-organized event: {}')
        #         return EventNotify(event_start_date_obj, event_end_date_obj)
    # return 'Ambient'


def EventNotify(event_start_date_obj, event_end_date_obj):
    now_obj = datetime.datetime.now()
    now_plus_minutes_obj = datetime.datetime.now() + timedelta(
        minutes=CAL_EVENT_CHECK_INTERVAL_MIN
    )
    starting_soon = (
        event_start_date_obj > now_obj and event_start_date_obj < now_plus_minutes_obj
    )
    in_progress = event_start_date_obj < now_obj and event_end_date_obj > now_obj
    if LOGGING:
        print("now_obj: {}".format(now_obj))
        print("now_plus_minutes_obj: {}".format(now_plus_minutes_obj))
        print("event_start_date_obj: {}".format(event_start_date_obj))
        print("event_end_date_obj: {}".format(event_end_date_obj))
        print("starting_soon: {}".format(starting_soon))
        print("in_progress: {}".format(in_progress))
    if starting_soon or in_progress:
        if LOGGING:
            print("GVC")
        return "GVC"
    else:
        if LOGGING:
            print("Ambient")
        return "Ambient"


def exit_handler():
    if LOGGING:
        print("Process terminated. Lights off.")
    bridge = Bridge(BRIDGE_IP, BRIDGE_USERNAME)
    hueLights = bridge.lights
    TurnOffLights(hueLights)


def main():
    atexit.register(exit_handler)
    # https://developers.google.com/calendar/quickstart/python
    # Initialize Calendar API
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)

    service = build("calendar", "v3", credentials=creds)

    # https://developers.meethue.com/develop/hue-api/lights-api/
    # https://github.com/quentinsf/qhue
    # Initialize Philips Hue Bridge
    bridge = Bridge(BRIDGE_IP, BRIDGE_USERNAME)
    hueLights = bridge.lights
    hueGroups = bridge.groups

    now_time = datetime.datetime.now().time()

    TurnOnLights(hueLights)

    while now_time:
        # Identify if there is a GVC coming up or not
        mode = GetCalendarEvents(service)
        SetLightMode(hueGroups, hueLights, mode)

        if LOGGING:
            print("Sleeping for {} seconds \n".format(LIGHT_CHANGE_INTERVAL_SEC))
        time.sleep(LIGHT_CHANGE_INTERVAL_SEC)
        now_time = datetime.datetime.now().time()

    TurnOffLights(hueLights)


if __name__ == "__main__":
    main()
