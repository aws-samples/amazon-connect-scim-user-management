import json
from json import dumps
import random
import string
import boto3
import logging
import urllib3
import os

logging.basicConfig(level = logging.INFO)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm_client = boto3.client('ssm')
http = urllib3.PoolManager()

lower = string.ascii_lowercase
num = string.digits
all = lower + num

PARAMETER_NAME = os.getenv("PARAMETER_NAME")

def send_response(event, context, response):
    '''Send a response to CloudFormation to handle the custom resource lifecycle.'''

    responseBody = { 
        'Status': response,
        'Reason': 'See details in CloudWatch Log Stream: ' + context.log_stream_name,
        'PhysicalResourceId': context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
    }

    print('RESPONSE BODY: \n' + dumps(responseBody))

    responseUrl = event['ResponseURL']
    json_responseBody = json.dumps(responseBody)
    headers = {
          'content-type' : '',
          'content-length' : str(len(json_responseBody))
    }
    try:
        response = http.request('PUT', responseUrl, headers=headers, body=json_responseBody)
        #response = response.send(responseUrl, data=json_responseBody, headers=headers)
        print ("Status code: " +response.reason)

    except Exception as e:
        print ("send(..) failed executing requests.put(..): " + str(e))

    return True

def lambda_handler(event, context):
    logger.info(event)
    key_length = int(event['ResourceProperties']['ApiLength'])
    if event['RequestType'] == 'Create':
        try:
            logger.info(f"Generating api key of length {key_length}")
            temp = random.sample(all,key_length)
            temppass = ''.join(temp)
            put_ssm_key = ssm_client.put_parameter(
                Name = PARAMETER_NAME, 
                Type= 'String', 
                Value = temppass,
                Overwrite = True
            )
            response = 'SUCCESS'
        except Exception as e:
            logger.info(f"Creating Service control policy failed because of {e}")
            response = 'FAILED'
        send_response(event, context, response)
    if event['RequestType'] == 'Update':
        response = 'SUCCESS'
        send_response(event, context, response)
    if event['RequestType'] == 'Delete':
        try:
            response = ssm_client.delete_parameters(
                Names=[
                    PARAMETER_NAME,
                ])
            response = 'SUCCESS'
            send_response(event, context, response)
        except Exception as e:
            logger.info(f"Deletion of parameter store because of {e}")
            response = 'FAILED'
            send_response(event, context, response)