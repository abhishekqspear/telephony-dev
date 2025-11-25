import os
import json
import requests
import uuid
from dotenv import load_dotenv
# import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Query, Request,Depends,Header
from fastapi.responses import PlainTextResponse,JSONResponse
import plivo
import urllib.parse
from datetime import datetime
from redis.asyncio.cluster import RedisCluster

app = FastAPI()
load_dotenv()
port = 8002

plivo_auth_id = os.getenv('PLIVO_AUTH_ID')
plivo_auth_token = os.getenv('PLIVO_AUTH_TOKEN')
plivo_phone_number = os.getenv('PLIVO_PHONE_NUMBER')

# Initialize Plivo client
plivo_client = plivo.RestClient(os.getenv('PLIVO_AUTH_ID'), os.getenv('PLIVO_AUTH_TOKEN'))

redis_host="clustercfg.voice-config-redis-micro-restore.hilh9d.aps1.cache.amazonaws.com"
# redis_host="redis"
redis_port="6379"
redis_password="piMfyp-tejkyg-8sarqi"
redis_client = RedisCluster(
    host=redis_host,
    port=redis_port,
    username="redis-user",
    password=redis_password,
    ssl=True,  # Enable SSL if connecting to AWS Elasticache Redis
    decode_responses=True
)

# redis_pool = redis.ConnectionPool.from_url("redis://redis:6379", decode_responses=True)
# redis_client = redis.Redis.from_pool(redis_pool)

def get_authorization_header(authorization: str = Header(None)):
    if authorization is None:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    return authorization

async def get_client_id(authorization):
    print(f"authorization {authorization}")
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header missing or invalid")
    
    token = authorization.split(" ")[1] 

    response= requests.get("https://beetlelabs.nyvioai.com/user-auth/user/me",headers={"Authorization":f"Bearer {token}"})

    if response.status_code != 200:
        raise Exception(f"Request failed:{response.status_code} {response.text}")

    data=response.json()
    client_id=data.get("clientId")
    return data


def populate_ngrok_tunnels():
    response = requests.get("http://ngrok:4040/api/tunnels")  # ngrok interface
    telephony_url, bolna_url = None, None

    if response.status_code == 200:
        data = response.json()

        for tunnel in data['tunnels']:
            if tunnel['name'] == 'plivo-app':
                telephony_url = tunnel['public_url']
            elif tunnel['name'] == 'bolna-app':
                bolna_url = tunnel['public_url'].replace('https:', 'wss:')

        return telephony_url, bolna_url
    else:
        print(f"Error: Unable to fetch data. Status code: {response.status_code}")


@app.post('/call')
async def make_call(request: Request,authorization: str = Depends(get_authorization_header)):
    context_uuid = str(uuid.uuid4())
    data_for_db=None
    print(f"Authorization: {authorization}")
    try:
        client_data = await get_client_id(authorization=authorization)
        role = client_data.get("role")
        user_id = client_data.get("clientId")

        call_details = await request.json()
        print(f"Call details {call_details}")

        agent_id = call_details.get('agent_id', None)
        call_context = call_details.get('call_context',None)
        client_id = call_context.get('client_id', None)
        plivo_phone_number = call_details.get('from_phone_number',None)
        call_context["recipient_phone_number"] = call_details.get('recipient_phone_number')
        # print(f"Equals :{client_id != user_id}")

        # if (client_id != user_id and role == "Super Admin") or client_id == user_id:
        #     print(f"Creating agent for client {client_id}")

        if plivo_phone_number is None:
            raise HTTPException(status_code=400, detail="From number not provided")

        if client_id is not None:
            user_id = client_id

        # telephony_host, bolna_host = populate_ngrok_tunnels()
        telephony_host, bolna_host = "https://beetlelabs.nyvioai.com/telephony","wss://beetlelabs.nyvioai.com/voice"
        plivo_answer_url = f"{telephony_host}/plivo_connect?bolna_host={bolna_host}&agent_id={agent_id}&client_id={user_id}"

        if call_context is not None:
            data_for_db=call_context
            plivo_answer_url = plivo_answer_url + "&context_uuid=" + context_uuid

        if not agent_id:
            raise HTTPException(status_code=404, detail="Agent not provided")

        if not call_details or "recipient_phone_number" not in call_details:
            raise HTTPException(status_code=404, detail="Recipient phone number not provided")

        if data_for_db is not None:
            await redis_client.set(context_uuid, json.dumps(data_for_db))

        print(f'telephony_host: {telephony_host}')
        print(f'bolna_host: {bolna_host}')
        print(f'plivo_answer_url: {plivo_answer_url}')

        # adding hangup_url since plivo opens a 2nd websocket once the call is cut.
        # https://github.com/bolna-ai/bolna/issues/148#issuecomment-2127980509
        call = plivo_client.calls.create(
            from_=plivo_phone_number,
            to_=call_details.get('recipient_phone_number'),
            answer_url=plivo_answer_url,
            hangup_url=f"{telephony_host}/plivo_hangup_callback?client_id={user_id}&detail_id={call_context.get('id',None)}",
            answer_method='POST')
        
        if data_for_db is not None:
            data_for_db["call_uuid"] = call.request_uuid
            await redis_client.set(context_uuid, json.dumps(data_for_db))
        
        print(f"Call created with: {call}")
        return JSONResponse({"status":"done","call_uuid":call.request_uuid}, status_code=200)

    except Exception as e:
        print(f"Exception occurred in make_call: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.post('/plivo_connect')
async def plivo_connect(request: Request):
    print(f"plivo connect called {request.query_params}")
    bolna_websocket_url=None
    try:
        query_params = dict(request.query_params)
        client_id=query_params["client_id"]
        context_uuid=query_params.get("context_uuid",None)
        if context_uuid is not None:
            bolna_websocket_url = f'{query_params["bolna_host"]}/chat/v1/{query_params["agent_id"]}/client/{client_id}?context_uuid={context_uuid}'
        else:
            bolna_websocket_url = f'{query_params["bolna_host"]}/chat/v1/{query_params["agent_id"]}/client/{client_id}'
        # del query_params["bolna_host"]
        # del query_params["agent_id"]
        # query_list = list(query_params.items())

        # print(query_list)
        # print(f"query_list {query_list}")

        # if len(query_list)>2:
        # bolna_websocket_url = bolna_websocket_url +"?context="+ ",,,".join(f"{key}={value}" for key, value in query_list[0:])

        # headers=",".join(f"X-PH-{key}={value}" for key, value in query_list[0:])
        
        print(f"websocket url->>>>>{bolna_websocket_url}")
        response = '''
        <Response>
            <Stream bidirectional="true" keepCallAlive="true">{}</Stream>
        </Response>
        '''.format(bolna_websocket_url)

        print(response)

        return PlainTextResponse(str(response), status_code=200, media_type='text/xml')

    except Exception as e:
        print(f"Exception occurred in plivo_connect: {e}")


@app.post('/plivo_hangup_callback')
async def plivo_hangup_callback(request:Request,client_id:int,detail_id:int):
    form_data = await request.form()
    callback_data = {key: value for key, value in form_data.items()}

    print(f"client id : {client_id} detail id : {detail_id}")
    
    save_record_url("","",client_id=client_id,call_detail_id=detail_id,detail_response=callback_data)
    return PlainTextResponse("", status_code=200)

def save_record_url(s3_record_url,s3_transcript_url,client_id=None,call_detail_id=None,detail_response=None):
    print("Save Callback called")
    record_data = {
        "transcript_url":s3_transcript_url,
        "recording_url":s3_record_url,
        "total_calls":1,
        "call_outcome":"",
        "client_id":client_id,
        "call_detail_id":call_detail_id,
    }

    if detail_response is not None:
        call_duration = detail_response["Duration"]
        call_direction = detail_response["Direction"]
        call_state = detail_response["HangupCauseName"]
        from_number = detail_response["From"]
        end_time = detail_response["EndTime"]
        answer_time = detail_response.get("AnswerTime", None)
        initiation_time = detail_response["StartTime"]
        hangup_cause_name = detail_response["HangupCause"]
        to_number = detail_response["To"]
        call_sid = detail_response["CallUUID"]

        if end_time:
            end_time=end_time+"+05:30"
            end_time = datetime.fromisoformat(end_time)
            end_time = end_time.isoformat()
        if answer_time:
            answer_time=answer_time+"+05:30"
            answer_time = datetime.fromisoformat(answer_time)
            answer_time = answer_time.isoformat()
        if initiation_time:
            initiation_time=initiation_time+"+05:30"
            initiation_time = datetime.fromisoformat(initiation_time)
            initiation_time = initiation_time.isoformat()

        record_data["call_sid"] = call_sid
        record_data["call_duration"] = call_duration
        record_data["direction"] = call_direction
        record_data["state"] = call_state
        record_data["from_number"] = from_number
        record_data["end_time"] = end_time
        record_data["answer_time"] = answer_time
        record_data["initiation_time"] = initiation_time
        record_data["hangup_cause_name"] = hangup_cause_name
        record_data["to_number"] = to_number

    try:
        if(record_data.get("state")=="No Answer" or record_data.get("state")=="Rejected" or record_data.get("state")=="Busy Line" or record_data.get("state")=="Failed"):
            print("Record saved:",record_data)
            response = requests.post("https://beetlelabs.nyvioai.com/marketing-ms/records",json=record_data,headers={"Content-Type":"application/json"})
            print(response.text)
    except Exception as e:
        print(f"Error:{e}")
