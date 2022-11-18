# pylint: disable=R0914
# pylint: disable=C0301
# pylint: disable=W0612
"""User management Lambda to manage connect users."""

import os
import re
import json
import logging
import boto3
import botocore

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

# boto3 service call
CONNECT_CLIENT = boto3.client('connect')

# Environment variaable
INSTANCE_ID = os.getenv("INSTANCE_ID")
DEFAULT_ROUTING_PROFILE = os.getenv('DEFAULT_ROUTING_PROFILE')

# The fuction to get connect user information


def get_connect_user(userid):
    """To get Connect user info."""
    user_info = {}
    user_list = []
    try:
        get_users = CONNECT_CLIENT.list_users(
            InstanceId=INSTANCE_ID,
            MaxResults=1000
        )
        user_list.extend(get_users['UserSummaryList'])
        for users in user_list['UserSummaryList']:
            if userid == users['Id']:
                user_info = {
                    "Username" : users['Username'],
                    "Id" : users['Id']
                }
        return user_info
    except botocore.exceptions.ClientError as error:
        LOGGER.error("Connect User Management Failure - Boto3 client error in UserManagementScimLambda while getting Connect user due to %s", error.response['Error']['Code'])     # noqa: E501
        raise error


# The fuction to Create connect user based on SCIM payload

def create_connect_user(body):
    """To create connect user based on scim payload."""
    try:
        user_info = json.loads(body)
        user_name = user_info['userName']
        first_name = user_info['name']['givenName']
        last_name = user_info['name']['familyName']
        security_profile = user_info["entitlements"]
        if "roles" in user_info:
            routing_profile_name = ''.join(user_info["roles"])
        else:
            routing_profile_name = DEFAULT_ROUTING_PROFILE
        sg_id_list = get_sg_id(security_profile)
        routing_id = get_routing_id(routing_profile_name)
        LOGGER.info("The security profile %s id: %s will be assigned to user %s", security_profile, sg_id_list, user_name)    # noqa: E501
        LOGGER.info("The routing profile ['%s'] id: %s will be assigned to user %s", routing_profile_name, routing_id, user_name)    # noqa: E501
        output = CONNECT_CLIENT.create_user(
            Username=user_name,
            IdentityInfo={
                'FirstName': first_name,
                'LastName': last_name
            },
            PhoneConfig={
                'PhoneType': 'SOFT_PHONE',
                'AutoAccept': False,
                'AfterContactWorkTimeLimit': 30
            },
            SecurityProfileIds=sg_id_list,
            RoutingProfileId=routing_id,
            InstanceId=INSTANCE_ID
        )
        user_info['id'] = output['UserId']
        return user_info
    except botocore.exceptions.ClientError as error:
        LOGGER.error("Connect User Management Failure - Boto3 client error in UserManagementScimLambda while creating Connect user due to %s", error.response['Error']['Code'])     # noqa: E501
        raise error


# The fuction to get connect security profile.


def get_sg_id(security_profile):
    """To get security profile id."""
    try:
        sg_id = []
        security_profile_list = CONNECT_CLIENT.list_security_profiles(
            InstanceId=INSTANCE_ID,
            MaxResults=1000
        )
        for each_security_profile in security_profile:
            for profile in security_profile_list['SecurityProfileSummaryList']:
                if each_security_profile == profile['Name']:
                    sg_id.append(profile['Id'])
        return sg_id
    except botocore.exceptions.ClientError as error:
        LOGGER.error("Connect User Management Failure - Boto3 client error in UserManagementScimLambda while getting SecurityProfile Id due to %s", error.response['Error']['Code'])       # noqa: E501
        raise error


# The fuction to get connect Routing profile Id.


def get_routing_id(routing_profile_name):
    """To get Routing profile id."""
    try:
        routing_id = ''
        routing_profile_list = CONNECT_CLIENT.list_routing_profiles(
            InstanceId=INSTANCE_ID,
            MaxResults=1000
        )
        for profile in routing_profile_list['RoutingProfileSummaryList']:
            if routing_profile_name == profile['Name']:
                routing_id = profile['Id']
        return routing_id
    except botocore.exceptions.ClientError as error:
        LOGGER.error("Connect User Management Failure - Boto3 client error in UserManagementScimLambda while getting Routing Profile Id due to %s", error.response['Error']['Code'])       # noqa: E501
        raise error


# SCIM response for user not found user case.


def user_not_found_response():
    """To send scim response when user not found."""
    return_response = {
        "id" : "urn:ietf:params:scim:api:messages:2.0:ListResponse",
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 0,
        "Resources": [],
        "startIndex": 1,
        "itemsPerPage": 20
    }
    return return_response

#SCIM response to send when user exist in Connect.


def build_scim_user(user) :
    """To send scim response when user exist."""
    LOGGER.info("The Existing user to build SCIM response %s",user)
    sg_entitlement = []

    try:
        user_list = CONNECT_CLIENT.list_users(
            InstanceId=INSTANCE_ID,
            MaxResults=1000
        )
        userid = user["Id"]
        for users in user_list['UserSummaryList']:
            if userid == users['Id']:
                get_user_info = CONNECT_CLIENT.describe_user(UserId=userid,
                                                             InstanceId=INSTANCE_ID)
                get_exist_sg_id = get_user_info['User']['SecurityProfileIds']
        for each_sg_info in get_exist_sg_id:
            get_sg_detail = CONNECT_CLIENT.describe_security_profile(
                    SecurityProfileId=each_sg_info,
                    InstanceId=INSTANCE_ID
            )
            get_sg_name = get_sg_detail['SecurityProfile']['SecurityProfileName']
            sg_entitlement.append(get_sg_name)

        user['entitlements'] = sg_entitlement
        scim_user = "{{\"schemas\":[\"urn:ietf:params:scim:schemas:core:2.0:User\",\"urn:ietf:params:scim:schemas:extension:enterprise:2.0:User\"], \"id\":\"{}\",\"externalId\":\"{}\",\"userName\":\"{}\",\"active\":true,\"meta\":{{\"resourceType\":\"User\"}},\"roles\":[]}}".format(user["Id"], user["Username"],  user["Username"])      # noqa: E501
        send_response = json.loads(scim_user)
        #update_entitlement = ast.literal_eval(send_response['entitlements'])
        return_response = {
                "schemas": [
                    "urn:ietf:params:scim:api:messages:2.0:ListResponse"
                ],
                "totalResults": 1,
                "Resources": [send_response],
                "startIndex": 1,
                "itemsPerPage": 20,
                "entitlements": sg_entitlement
            }
        return return_response
    except botocore.exceptions.ClientError as error:
        LOGGER.error("Connect User Management Failure - Boto3 client error in UserManagementScimLambda while building scim response due to %s", error.response['Error']['Code'])     # noqa: E501
        raise error

# SCIM dumy response for group request.


def dummy_group_response():
    """To send dummy group response."""
    return_message = {
        "id" : "urn:ietf:params:scim:api:messages:2.0:ListResponse",
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 1,
        "Resources": [],
        "startIndex": 1,
        "itemsPerPage": 20
    }
    return return_message

# The function to update connect user during PUT request.


def update_connect_user(userid, body):
    """To update connect user."""
    user_info = json.loads(body)
    sg_entitlement = []

    try:
        user_list = CONNECT_CLIENT.list_users(
            InstanceId=INSTANCE_ID,
            MaxResults=1000
        )
        for users in user_list['UserSummaryList']:
            if userid == users['Id']:
                get_updated_sg_info = get_sg_id(user_info['entitlements'])
                LOGGER.info("The updated list of security profile %s for the user %s", get_updated_sg_info, userid)     # noqa: E501
                CONNECT_CLIENT.update_user_security_profiles(SecurityProfileIds=get_updated_sg_info,        # noqa: E501
                                                             UserId=userid,         # noqa: E501
                                                             InstanceId=INSTANCE_ID)       # noqa: E501
        for each_sg_info in get_updated_sg_info:
            get_sg_detail = CONNECT_CLIENT.describe_security_profile(SecurityProfileId=each_sg_info, InstanceId=INSTANCE_ID)   # noqa: E501
            get_sg_name = get_sg_detail['SecurityProfile']['SecurityProfileName']     # noqa: E501
            sg_entitlement.append(get_sg_name)
        user_info["id"] = userid
        user_info["entitlements"] = sg_entitlement
        return user_info
    except botocore.exceptions.ClientError as error:
        LOGGER.error("Connect User Management Failure - Boto3 client error in UserManagementScimLambda while updating Connect user due to %s", error.response['Error']['Code'])     # noqa: E501
        raise error

# Main Lambda function


def lambda_handler(event, context):
    """The handler for the user management."""
    LOGGER.info("Received event is %s",json.dumps(event))
    LOGGER.info("Received context is %s",context)
    body = event['body']
    method = event['httpMethod']

    # Get method user management action

    if method == 'GET':
        uid = ""
        # No Group being used okta integration
        if (event['pathParameters'] and event['pathParameters']['Users'] and event['pathParameters']['Users'] =='Groups'):     # noqa: E501
            message = dummy_group_response()
            return {
                "statusCode" : 200,
                "body": json.dumps(message),
                "headers": {
                        'Content-Type': 'application/json',
            }
        }
        if (event['pathParameters'] and event['pathParameters']['Users'] and event['pathParameters']['Users'] !='Users'):      # noqa: E501
            uid = event.pathParameters.proxy.split("/")[1]
        if "externalId eq" in event['queryStringParameters']['filter']:
            user_list = re.split(r'["\\\/\"]',event['queryStringParameters']['filter'] )        # noqa: E501
            uid = user_list[-1]
        if "userName eq" in event['queryStringParameters']['filter']:
            user_list = re.split(r'["\\\/\"]',event['queryStringParameters']['filter'] )        # noqa: E501
        uid = user_list[-1]
        if uid == "":
            uid = user_list[-2]
        LOGGER.info("The user in the request is %s",uid)
        if uid != "":
            user_info = get_connect_user(uid)
            if user_info:
                scim_user = build_scim_user(user_info)
                LOGGER.info("Method:GET for existing user - SCIM User Response ==========> %s",json.dumps(scim_user))    # noqa: E501
            else:
                scim_user = user_not_found_response()
                LOGGER.info("Method:GET - SCIM User Response ==========> %s",json.dumps(scim_user))      # noqa: E501
            return {
                'statusCode': 200,
                'body': json.dumps(scim_user) ,
                'headers': {
                    'Content-Type': 'application/json',
                }
            }
        else:
            LOGGER.error("No user Id provided")

    # POST method user management action

    if method == 'POST':
        LOGGER.info("Method:POST - " + event['pathParameters']['Users'])
        if (event['pathParameters'] and event['pathParameters']['Users'] and event['pathParameters']['Users'] =='Groups'):     # noqa: E501
            message = dummy_group_response()
            return {
            "statusCode" : 200,
            "body": json.dumps(message),
            "headers": {
                'Content-Type': 'application/json',
            }
        }
        LOGGER.info("Method:POST - Add User %s",body)
        user_to_create = create_connect_user(body)
        LOGGER.info(user_to_create)
        if user_to_create:
            LOGGER.info("Scim return response for POST ======> %s",json.dumps(user_to_create))      # noqa: E501
            return {
                "statusCode": 200,
                "body": json.dumps(user_to_create),
                "headers": {
                    'Content-Type': 'application/json',
                }
            }
        else:
            return {
                "statusCode": 200,
                "body": '',
                "headers": {
                    'Content-Type': 'application/json',
                }
            }

    # PATCH method user management action
    if method == 'PATCH':
        if (event['pathParameters'] and event['pathParameters']['Users'] and event['pathParameters']['Users'] =='Groups'):     # noqa: E501
            message = dummy_group_response()
            return {
                "statusCode" : 200,
                "body": json.dumps(message),
                "headers": {
                        'Content-Type': 'application/json',
            }
        }
        LOGGER.info("Method:PATCH - Update or Delete User %s",body)
        uid = ''
        if "userName eq" in event['queryStringParameters']['filter']:
            user_list = re.split(r'["\\\/]', event['queryStringParameters']['filter'] )    # noqa: E501
            uid = user_list[-1]
        user_update_info = json.loads(body)
        for info in user_update_info['Operations']:
            user_status = info['value']['active']
        LOGGER.info("User status is ..... %s",user_status)
        if user_status == False:
            delete_response = CONNECT_CLIENT.delete_user(
                InstanceId=INSTANCE_ID,
                UserId=uid
            )
            user_update_info["id"] = uid
            LOGGER.info("The SCIM retur response for PATCH %s",json.dumps(user_update_info))     # noqa: E501
            return {
                "statusCode" : 200,
                "body": json.dumps(user_update_info),
                "headers": {
                        'Content-Type': 'application/json',
            }
        }

    # PUT method user management action
    if method == "PUT":
        if (event['pathParameters'] and event['pathParameters']['Users'] and event['pathParameters']['Users'] =='Groups'):     # noqa: E501
            message = dummy_group_response()
            return {
                "statusCode" : 200,
                "body": json.dumps(message),
                "headers": {
                        'Content-Type': 'application/json',
            }
        }
        LOGGER.info("Method:PUT - Update User attributes")
        if "userName eq" in event['queryStringParameters']['filter']:
            user_list = re.split(r'["\\\/]',event['queryStringParameters']['filter'] )     # noqa: E501
            uid = user_list[-1]
        user_update = update_connect_user(uid, body)
        LOGGER.info("The Scim return response for PUT ======> %s",json.dumps(user_update))    # noqa: E501
        return {
            "statusCode" : 200,
            "body": json.dumps(user_update),
            "headers": {
                'Content-Type': 'application/json'
            }
        }