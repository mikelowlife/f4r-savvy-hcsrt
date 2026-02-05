import time
import random
import json
import requests
import hmac
import hashlib
import base64
import threading
import re
from datetime import datetime, timedelta
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# API Connection params
API_DOMAIN = "api-goimek.savvyds.com"
API_URL = "https://" + API_DOMAIN + "/v2"
API_MACHINES_URL = API_URL + "/machines"
API_INDICATORS_FILTER = "/indicators?summaryMode=1"
API_INDICATOR_DATA_FILTER = "/data?indicators="
API_GROUPS_FILTER = "/groups"
API_PLANNERS_URL = API_URL + "/planners"


API_KEY = "Rc3th6BBR6"
API_SECRET = "ZAguzlNLLY4YkCpTR8Xe"
global AAS_PROPERTY_VALUES
AAS_PROPERTY_VALUES=dict()

#AUXILIARY FUNCTIONS
def calculateRFC2104HMAC(data, secret):
    dig = hmac.new(secret,data,hashlib.sha1).digest()
    return base64.b64encode(dig).decode()

def modify_nested_value(data, target_key, suffix_to_append):
    if isinstance(data, dict):
        # Iterate through dictionary items
        for key, value in data.items():
            if key == target_key:
                # --- Found the target key ---
                # Convert original value to string and append the suffix
                try:
                    # Check if it's already a string to avoid unnecessary conversion logic if desired
                    # if isinstance(value, str):
                    #     data[key] = value + suffix_to_append
                    # else:
                    # Always convert to string before appending for consistency
                    data[key] = str(value) + suffix_to_append
                    # print(f"DEBUG: Modified key '{key}': new value '{data[key]}'") # Optional debug print
                except TypeError:
                    # Handle cases where conversion/appending might fail unexpectedly
                    print(f"Warning: Could not append suffix to value of type {type(value)} for key '{key}'")

            # --- Recurse into nested structures ---
            # If the value is another dictionary or a list, recurse into it
            elif isinstance(value, (dict, list)):
                modify_nested_value(value, target_key, suffix_to_append)
            # We don't recurse into tuples as they are immutable

    elif isinstance(data, list):
        # If it's a list, iterate through its items
        for item in data:
             # If an item in the list is a dictionary or another list, recurse into it
            if isinstance(item, (dict, list)):
                modify_nested_value(item, target_key, suffix_to_append)
            # We don't recurse into tuples here either

def clone_and_modify_nested_value(data, target_key, suffix_to_append):
    if isinstance(data, dict):
        # Create a new dictionary for the clone
        new_dict = {}
        for key, value in data.items():
            if key == target_key:
                # Apply modification to the value being placed in the new dict
                try:
                    if suffix_to_append == "":
                        new_dict[key] = re.sub(r'\d+','',str(value))
                    else:
                        new_dict[key] = str(value) + suffix_to_append
                except TypeError:
                    # Handle cases where conversion/appending might fail
                    print(f"Warning: Could not append suffix to value of type {type(value)} for key '{key}'. Cloning value as is.")
                    # Recursively clone the problematic value itself without modification
                    new_dict[key] = clone_and_modify_nested_value(value, target_key, suffix_to_append)
            else:
                # Recursively clone/modify the value and store it in the new dict
                new_dict[key] = clone_and_modify_nested_value(value, target_key, suffix_to_append)
        return new_dict # Return the newly created dictionary

    elif isinstance(data, list):
        # Create a new list by recursively cloning/modifying each item
        # Use a list comprehension for conciseness
        return [clone_and_modify_nested_value(item, target_key, suffix_to_append) for item in data]

    elif isinstance(data, tuple):
         # Create a new tuple by recursively cloning/modifying each item
         # Note: This returns a new tuple, preserving immutability concept
        return tuple(clone_and_modify_nested_value(item, target_key, suffix_to_append) for item in data)

    else:
        # Base case: return immutable values (or values not needing further traversal) directly.
        # These values are effectively copied when assigned to the new structure.
        return data


def api_wo_to_aas_wo(api_wo,aas_wo):
    for wo_prop in aas_wo['value']:
        if wo_prop['idShort'].startswith('Identifier'):
            wo_prop['value'] = api_wo['workOrderIdentifier']
        elif wo_prop['idShort'].startswith('Description'):
            wo_prop['value'] = api_wo.get('workOrderDescription','-')
        elif wo_prop['idShort'].startswith('Customer'):
            wo_prop['value'] = api_wo.get('workOrderCustomer','-')
        elif wo_prop['idShort'].startswith('DueDate'):
            wo_prop['value'] = api_wo.get('workOrderDueDate','-')
        elif wo_prop['idShort'].startswith('ProcessRoute'):
            for rt_prop in wo_prop['value']:
                if rt_prop['idShort'].startswith('ProcessName'):
                    rt_prop['value'] = api_wo['workOrderItems'][0]['itemProcessName'] #de momento solo 1 item por WO
                elif rt_prop['idShort'].startswith('OperationSequence'):
                    new_ops = []
                    op = rt_prop['value'][0] # siempre hay al menos una operacion
                    i = 1
                    for api_op in api_wo['workOrderItems'][0]['itemOperations']:
                        new_op = clone_and_modify_nested_value(op,"idShort",str(i))
                        # poblar las propiedades de operaciones
                        for op_prop in new_op['value']:
                            if op_prop['idShort'].startswith('OperationName'):
                                op_prop['value'] = api_op['operationName']
                            elif op_prop['idShort'].startswith('StartTime'):
                                op_prop['value'] = api_op['operationStartTime']
                            elif op_prop['idShort'].startswith('Duration'):
                                op_prop['value'] = api_op['operationTotalDuration']
                            elif op_prop['idShort'].startswith('Status'):
                                op_prop['value'] = api_op['operationStatus']
                            elif op_prop['idShort'].startswith('RequiredResources'):
                                for res_prop in op_prop['value']:
                                    if res_prop['idShort'].startswith('WorkCenter') and 'scheduleMachine' in api_op['operationWorkCenter']:
                                        res_prop['value'][0]['value'] = api_op['operationWorkCenter']['scheduleMachine']['scheduleMachineName']
                                        res_prop['value'][1]['value'] = api_op['operationWorkCenter']['scheduleMachine']['scheduleMachineOperators'][0]['operatorName']
                                    elif res_prop['idShort'].startswith('BusinessPartner') and 'businessPartner' in api_op['operationWorkCenter']:
                                        res_prop['value'][0]['value'] = api_op['operationWorkCenter']['businessPartner']['businessPartnerName']

                        new_ops.append(new_op)
                        i = i + 1
                    rt_prop['value'] = new_ops
    return aas_wo


#SAVVY API FUNCTIONS
def getSAVVYMachines():
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    blurb = "GET\ntext/plain\n" + sequence + "\n/v2/machines"
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"text/plain"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Querying API for machine list at " + API_MACHINES_URL)
    response = savvy_session.get(API_MACHINES_URL,verify=False)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
        #print(response.content)
        return json.loads(response.content)
    else:
        print(str(datetime.now()) + ": No data")
        return dict()

def getSAVVYMachineIndicators(machineID):
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    blurb = "GET\ntext/plain\n" + sequence + "\n/v2/machines/" + machineID + "/indicators"
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"text/plain"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Querying API for indicator list at " + API_MACHINES_URL + "/" + machineID + "/indicators")
    response = savvy_session.get(API_MACHINES_URL + "/" + machineID + "/indicators",verify=False)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
        #print(response.content)
        return json.loads(response.content)
    else:
        print(str(datetime.now()) + ": No data")
        return dict()

def downloadSAVVYMachineData(indicatorID,startDate=None,endDate=None):
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    endpointUrl = "/v2/data?indicators="+indicatorID
    if(not (startDate is None) and  not (endDate is None)):
        endpointUrl = "/v2/data?indicators=" + indicatorID + "&from=" + str(startDate) + '&to=' + str(endDate)

    blurb = "GET\ntext/plain\n" + sequence + "\n" + endpointUrl
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"text/plain"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Querying API for indicator data download at " + "https://"+API_DOMAIN+endpointUrl)
    response = savvy_session.get("https://"+API_DOMAIN+endpointUrl, verify=False, stream=True)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
       #print(response.content)
       return json.loads(response.content)
    else:
        if response.status_code==429:
            return downloadSAVVYMachineData(indicatorID,startDate,endDate)
        else:
            print(str(datetime.now()) + ": No data")
            return dict()


def getSAVVYPlanners():
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    blurb = "GET\ntext/plain\n" + sequence + "\n/v2/planners"
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"text/plain"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Querying API for planners list at " + API_PLANNERS_URL)
    response = savvy_session.get(API_PLANNERS_URL,verify=False)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
        #print(response.content)
        return json.loads(response.content)
    else:
        print(str(datetime.now()) + ": No data")
        return dict()

def checkSAVVYSchedule(plannerID):
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    blurb = "GET\ntext/plain\n" + sequence + "\n/v2/planners/" + plannerID + "/schedule"
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"text/plain"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Requesting schedule status from API at " + API_PLANNERS_URL + "/" + plannerID +"/schedule")
    response = savvy_session.get(API_PLANNERS_URL + "/" + plannerID +"/schedule",verify=False)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
        print(response.content)
        return json.loads(response.content)
    else:
        print(str(datetime.now()) + ": No data")

def clearSAVVYSchedule(plannerID):
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    blurb = "PUT\ntext/plain\n" + sequence + "\n/v2/planners/" + plannerID + "/schedule/clear"
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"text/plain"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Requesting API to clear schedule " + API_PLANNERS_URL + "/" + plannerID + "/schedule/clear")
    response = savvy_session.put(API_PLANNERS_URL + "/" + plannerID + "/schedule/clear",verify=False)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
        #print(response.content)
        return json.loads(response.content)
    else:
        print(str(datetime.now()) + ": No data")

def modifySAVVYSchedule(plannerID,delete_wos,include_wos):
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    blurb = "PUT\napplication/json\n" + sequence + "\n/v2/planners/" + plannerID + "/schedule/modify"
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"application/json"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Requesting API to modify schedule " + API_PLANNERS_URL + "/" + plannerID +"/schedule/modify")
    print("WOs to remove from schedule: ")
    updated_wos=dict()
    updated_wos["include_wos"] = list()
    updated_wos["delete_wos"] = list()
    for d_wo in delete_wos:
        print(d_wo["workOrderIdentifier"])
        updated_wos["delete_wos"].append(d_wo["workOrderIdentifier"])
    print("WOs to include in schedule: ")
    for i_wo in include_wos:
        print(i_wo["workOrderIdentifier"])
        updated_wos["include_wos"].append(i_wo["workOrderIdentifier"])
    print(updated_wos)
    response = savvy_session.put(API_PLANNERS_URL + "/" + plannerID +"/schedule/modify",data=json.dumps(updated_wos),headers={'Content-Type':'application/json'},verify=False)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
        print(response.content)
        return json.loads(response.content)
    else:
        print(str(datetime.now()) + ": No data")


def stopSAVVYRescheduling(plannerID):
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    blurb = "PUT\ntext/plain\n" + sequence + "\n/v2/planners/" + plannerID + "/schedule/stop"
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"text/plain"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Requesting API to stop rescheduling algorithm " + API_PLANNERS_URL + "/" + plannerID +"/schedule/stop")
    response = savvy_session.put(API_PLANNERS_URL + "/" + plannerID +"/schedule/stop",verify=False)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
        #print(response.content)
        return json.loads(response.content)
    else:
        print(str(datetime.now()) + ": No data")

def getSAVVYWorkOrders(plannerID,scheduled="1"):
    time.sleep(1)
    savvy_session = requests.Session()
    sequence = str(int(datetime.now().timestamp()))
    blurb = "GET\ntext/plain\n" + sequence + "\n/v2/planners/" + plannerID + "/workorders?status="+scheduled
    auth = calculateRFC2104HMAC(bytes(blurb,'latin-1'), bytes(API_SECRET,'latin-1'))
    savvy_session.headers.update({"content-type":"text/plain"})
    savvy_session.headers.update({"X-M2C-Sequence":sequence})
    savvy_session.headers.update({"Authorization":"M2C " + API_KEY + ":" + auth})
    #savvy_session.proxies = {"https":"http://192.168.40.10:3128"}
    print(str(datetime.now()) + ": Querying API for work orders list at " + API_PLANNERS_URL + "/" + plannerID + "/workorders?status="+scheduled)
    response = savvy_session.get(API_PLANNERS_URL + "/" + plannerID + "/workorders?status="+scheduled,verify=False)
    print(str(datetime.now()) + ": Response received (Status code " + str(response.status_code) + ")")
    if response.ok:
        #print(response.content)
        return json.loads(response.content)
    else:
        print(str(datetime.now()) + ": No data")
        return dict()

def apiInit():
    planners = getSAVVYPlanners()
    plannerID = ""
    machines = getSAVVYMachines()
    scheduled_work_orders = dict()
    unscheduled_work_orders = dict()
    for planner in planners:
        if "F4R" in planner['plannerName']:
            plannerID = planner['plannerId']
            print("F4R planner found: " + planner['plannerName'])
            scheduled_work_orders = getSAVVYWorkOrders(planner['plannerId'])
            unscheduled_work_orders = getSAVVYWorkOrders(planner['plannerId'],"0")
            break
    if len(scheduled_work_orders)>0:
        for work_order in scheduled_work_orders:
            print("  Scheduled Work Order: " + work_order['workOrderIdentifier'])
            for item in work_order['workOrderItems']:
                #print("    Item in Work Order: " + item['itemName'])
                for operation in item['itemOperations']:
                    #print("      Operation for item: " + operation['operationName'])
                    if "scheduleMachine" in operation['operationWorkCenter']: #shopfloor operation
                        #print("        Machine assigned to operation: " + operation['operationWorkCenter']['scheduleMachine']['scheduleMachineName'])
                        operators = []
                        for operator in operation['operationWorkCenter']['scheduleMachine']['scheduleMachineOperators']:
                            operators.append(operator['operatorName'])
                        #print("          Operators compatible with machine: "+ str(operators))
                    elif len(operation['operationWorkCenter']['businessPartner'])>0: #business partner operation
                        print("        Business partner for outsourced operation: "+ operation['operationWorkCenter']['businessPartner']['businessPartnerName'])
    if len(unscheduled_work_orders)>0:
        for work_order in unscheduled_work_orders:
            print("  Unscheduled Work Order: " + work_order['workOrderIdentifier'])


    return scheduled_work_orders, unscheduled_work_orders, machines, plannerID


# AAS Connection params
#AAS_DOMAIN = "82.223.202.158"
AAS_DOMAIN = "flex4res.savvyds.com"
AAS_ENV = "https://" + AAS_DOMAIN + "/aas-env"
AAS_ENV_SHELLS = AAS_ENV + "/shells"
AAS_ENV_SUBMODELS = AAS_ENV + "/submodels"
AAS_REGISTRY = "https://" + AAS_DOMAIN + "/aas-registry"
AAS_SM_REGISTRY = "https://" + AAS_DOMAIN + "/sm-registry"
AAS_SM_REGISTRY_SUBMODELS = AAS_SM_REGISTRY + "/submodel-descriptors"
AAS_DISCOVERY = "https://" + AAS_DOMAIN + "/aas-discovery"

def aasInit():
    # Query AAS Server
    shells = dict()
    submodels = dict()
    aas_session = requests.Session()
    #aas_session.proxies = {"http":"http://192.168.40.10:3128"}

    resilience_aas = dict()
    resilience_aas_found = False
    resilience_aas_endpoint=""
    prod_schedule_aas = dict()
    prod_schedule_aas_found = False
    prod_schedule_aas_endpoint=""
    response = aas_session.get(AAS_SM_REGISTRY_SUBMODELS)
    if response.ok:
        submodels = json.loads(response.content)
    print("AAS Registry Submodels: " + str(len(submodels)))
    for submodel in submodels['result']:
        #print(submodel['idShort'] + " --> " + submodel['endpoints'][0]['protocolInformation']['href'])
        if submodel['idShort'] == 'ProductionSchedule':
            response = aas_session.get(submodel['endpoints'][0]['protocolInformation']['href'])
            if response.ok:
                sm_data = json.loads(response.content)
                if sm_data['kind'] == 'Template':
                    prod_schedule_smt_found = True
                    prod_schedule_smt = sm_data
                elif sm_data['kind'] == 'Instance':
                    prod_schedule_aas_found = True
                    prod_schedule_aas = sm_data
                    prod_schedule_aas_endpoint = submodel['endpoints'][0]['protocolInformation']['href']
        elif submodel['idShort'] == 'Resilience':
            response = aas_session.get(submodel['endpoints'][0]['protocolInformation']['href'])
            if response.ok:
                sm_data = json.loads(response.content)
                if sm_data['kind'] == 'Instance':
                    resilience_aas_found = True
                    resilience_aas = sm_data
                    resilience_aas_endpoint = submodel['endpoints'][0]['protocolInformation']['href']

    if prod_schedule_aas_found:
        print("Submodel instance found for production schedule: " + prod_schedule_aas['idShort'])
        print("Number of elements in submodel: " + str(len(prod_schedule_aas['submodelElements'])))

        for sme in prod_schedule_aas['submodelElements']:
            if sme['idShort'] == 'WorkOrder' and len(sme['value'])>0:
                work_order_id = ''
                new_work_order = sme
                for value in sme['value']:
                    if value['idShort'] == 'Identifier':
                        work_order_id = value['value']
    if resilience_aas_found:
        print("Submodel instance found for resilience: " + resilience_aas['idShort'])
        print("Number of elements in submodel: " + str(len(resilience_aas['submodelElements'])))

    else:
        print("AAS for production schedule not found")

    return prod_schedule_aas, prod_schedule_aas_endpoint, resilience_aas, resilience_aas_endpoint

############# MAIN #############

### CHECK RESILIENCE AAS & RESCHEDULE ###
aas_session = requests.Session()
#aas_session.proxies = {"http":"http://192.168.40.10:3128"}
schedule,s_endpoint,resilience,r_endpoint=aasInit()
resilience_risk_calculation = ""
resilience_risk_threshold = 5
for sme in resilience['submodelElements']:
        if sme['idShort'] == 'ResilienceKPI':
            for kpi in sme['value']:
                if kpi['idShort'] == 'ResilienceCalculation':
                    resilience_risk_calculation = kpi['value']
                    break
            break
print("Current resilience calculation: " + resilience_risk_calculation)
if int(resilience_risk_calculation) > resilience_risk_threshold:
    print("Current schedule resilience risk calculation exceeds threshold, rescheduling...")
    s_wos,u_wos,mqs,plannerID=apiInit()
    sched_status = checkSAVVYSchedule(plannerID)
    print("Schedule status: " + sched_status['plannerScheduleStatus'])
    while(not sched_status['plannerScheduleStatus'].endswith('synced')):
        if sched_status['plannerScheduleStatus'] == 'rescheduling':
            print("Stopping GA rescheduling...")
            stopSAVVYRescheduling(plannerID)
            time.sleep(5)
            sched_status = checkSAVVYSchedule(plannerID)
            print("Schedule status: " + sched_status['plannerScheduleStatus'])
        elif sched_status['plannerScheduleStatus'] == 'syncing':
            print("Waiting 10 seconds for schedule to sync...")
            time.sleep(10)
            sched_status = checkSAVVYSchedule(plannerID)
            print("Scheduling status: " + sched_status['plannerScheduleStatus'])

    if(len(s_wos)>0):
        clearSAVVYSchedule(plannerID)
        time.sleep(30)
        print("Current schedule cleared")
    #    s_wos = getSAVVYWorkOrders(plannerID,"1")
    #    u_wos = getSAVVYWorkOrders(plannerID,"0")



    modifySAVVYSchedule(plannerID,u_wos,s_wos)
    print("Rescheduling triggered; waiting 60 seconds for GA to kick in...")
    time.sleep(60)
    sched_status = checkSAVVYSchedule(plannerID)
    print("Schedule status: " + sched_status['plannerScheduleStatus'])
    while(sched_status['plannerScheduleStatus'] == 'rescheduling'):
        time.sleep(5)
        sched_status = checkSAVVYSchedule(plannerID)
        print("Schedule status: " + sched_status['plannerScheduleStatus'])


    print("Production schedule has been modified!")
    s_wos = getSAVVYWorkOrders(plannerID,"1")
    print(json.dumps(s_wos,indent=4))

### UPDATE SCHEDULE & RESILIENCE AAS ###
s_wos,u_wos,mqs,plannerID=apiInit()
schedule,s_endpoint,resilience,r_endpoint=aasInit()

# Clean first WO entry to use as base
base_wo=clone_and_modify_nested_value(schedule['submodelElements'][0],"idShort","")
schedule['submodelElements'].clear()
# Populate AAS with as many scheduled WOs as returned by API
i=1
for s_wo in s_wos:
    new_wo=clone_and_modify_nested_value(base_wo,"idShort",str(i))
    # modify WOs according to info from API
    updated_wo=api_wo_to_aas_wo(s_wo,new_wo)
    schedule['submodelElements'].append(updated_wo)
    i=i+1

# Leave default if no WOs returned from API
if len(schedule['submodelElements'])==0:
    schedule['submodelElements'].append(base_wo)

#print(json.dumps(schedule,indent=4))
print("Updating Production Schedule AAS Submodel on endpoint "+s_endpoint)

response = aas_session.put(s_endpoint,data=json.dumps(schedule),headers={'Content-Type':'application/json'})
if response.ok:
    print("AAS Production Schedule Submodel updated")
    #resilience calculation and AAS update
    resilience_risk_calculation=0
    for mq in mqs:
        idmq = mq['machineId']
        mq_inds = getSAVVYMachineIndicators(idmq)
        risk_ind=""
        risk_value=""
        for mq_ind in mq_inds:
            if mq_ind['indicatorName'] == 'Risk_machine':
                risk_ind = mq_ind['indicatorId']
                break
        if len(risk_ind)>0:
            #risk_data = downloadSAVVYMachineData(risk_ind,1743465600000,1743552000000)
            risk_data = downloadSAVVYMachineData(risk_ind,1744070400000,1744156800000)
            if len(risk_data['data'])>0 and len(risk_data['data'][0]['data'])>0 and len(risk_data['data'][0]['data'][0]['data'])>0:
                risk_value = risk_data['data'][0]['data'][0]['data'][0][risk_ind]
                resilience_risk_calculation = resilience_risk_calculation + int(risk_value)


            else:
                print("No risk value data available for machine " + mq['machineName'])

    #print(resilience)
    for sme in resilience['submodelElements']:
        if sme['idShort'] == 'ResilienceKPI':
            for kpi in sme['value']:
                if kpi['idShort'] == 'ResilienceCalculation':
                    kpi['value'] = str(resilience_risk_calculation)
                    break
            break
    print("Updating Resilience SubModel at " + r_endpoint)
    response = aas_session.put(r_endpoint,data=json.dumps(resilience),headers={'Content-Type':'application/json'})
    if response.ok:
        print("Resilience Submodel updated")
    else:
        print(response.request.headers)

else:
    print("Could not update Production Schedule Submodel: "+str(response.status_code))
    print(response.request.headers)




