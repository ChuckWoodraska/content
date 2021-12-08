import demistomock as demisto
from CommonServerPython import *

''' IMPORTS '''
import requests
import ipaddress
import dateparser
import tempfile
from typing import Tuple

# Disable insecure warnings
requests.packages.urllib3.disable_warnings()

''' CONSTANTS '''

ALERT_TITLE = 'Prisma Cloud Compute Alert - '
ALERT_TYPE_VULNERABILITY = 'vulnerability'
ALERT_TYPE_COMPLIANCE = 'compliance'
ALERT_TYPE_AUDIT = 'audit'
# this is a list of known headers arranged in the order to be displayed in the markdown table
HEADERS_BY_NAME = {
    'vulnerabilities': ['severity', 'cve', 'status', 'packages', 'sourcePackage', 'packageVersion', 'link'],
    'entities': ['name', 'containerGroup', 'resourceGroup', 'nodesCount', 'image', 'status', 'runningTasksCount',
                 'activeServicesCount', 'version', 'createdAt', 'runtime', 'arn', 'lastModified', 'protected'],
    'compliance': ['type', 'id', 'description']
}
MAX_API_LIMIT = 50

''' COMMANDS + REQUESTS FUNCTIONS '''


class PrismaCloudComputeClient(BaseClient):
    def __init__(self, base_url, verify, project, proxy=False, ok_codes=tuple(), headers=None, auth=None):
        """
        Extends the init method of BaseClient by adding the arguments below,

        verify: A 'True' or 'False' string, in which case it controls whether we verify
            the server's TLS certificate, or a string that represents a path to a CA bundle to use.
        project: A projectID string, set in the integration parameters.
            the projectID is saved under self._project
        """

        self._project = project

        if verify in ['True', 'False']:
            super().__init__(base_url, str_to_bool(verify), proxy, ok_codes, headers, auth)
        else:
            # verify points a path to certificate
            super().__init__(base_url, True, proxy, ok_codes, headers, auth)
            self._verify = verify

    def api_request(
        self, method, url_suffix, full_url=None, headers=None, auth=None, json_data=None, params=None, data=None,
        files=None, timeout=10, resp_type='json', ok_codes=None, **kwargs
    ):
        """
        A wrapper method for the http request.
        """
        if method == 'PUT':
            resp_type = 'text'

        return self._http_request(
            method=method, url_suffix=url_suffix, full_url=full_url, headers=headers, auth=auth, json_data=json_data,
            params=params, data=data, files=files, timeout=timeout, resp_type=resp_type, ok_codes=ok_codes, **kwargs
        )

    def _http_request(self, method, url_suffix, full_url=None, headers=None,
                      auth=None, json_data=None, params=None, data=None, files=None,
                      timeout=10, resp_type='json', ok_codes=None, **kwargs):
        """
        Extends the _http_request method of BaseClient.
        If self._project is available, a 'project=projectID' query param is automatically added to all requests.
        """
        # if project is given add it to params and call super method
        if self._project:
            params = params or {}
            params.update({'project': self._project})

        return super()._http_request(method=method, url_suffix=url_suffix, full_url=full_url, headers=headers,
                                     auth=auth, json_data=json_data, params=params, data=data, files=files,
                                     timeout=timeout, resp_type=resp_type, ok_codes=ok_codes, **kwargs)

    def test(self):
        """
        Calls the fetch alerts endpoint with to=epoch_time to check connectivity, authentication and authorization
        """
        return self.list_incidents(to_=time.strftime('%Y-%m-%d', time.gmtime(0)))

    def list_incidents(self, to_=None, from_=None):
        """
        Sends a request to fetch available alerts from last call
        No need to pass here TO/FROM query params, the API returns new alerts from the last request
        Can be used with TO/FROM query params to get alerts in a specific time period
        REMARK: alerts are deleted from the endpoint once were successfully fetched
        """
        params = {}
        if to_:
            params['to'] = to_
        if from_:
            params['from'] = from_

        # If the endpoint not found, fallback to the previous demisto-alerts endpoint (backward compatibility)
        try:
            return self._http_request(
                method='GET',
                url_suffix='xsoar-alerts',
                params=params
            )
        except Exception as e:
            if '[404]' in str(e):
                return self._http_request(
                    method='GET',
                    url_suffix='demisto-alerts',
                    params=params
                )
            raise e


def str_to_bool(s):
    """
    Translates string representing boolean value into boolean value
    """
    if s == 'True':
        return True
    elif s == 'False':
        return False
    else:
        raise ValueError


def translate_severity(sev):
    """
    Translates Prisma Cloud Compute alert severity into Demisto's severity score
    """

    sev = sev.capitalize()

    if sev == 'Critical':
        return 4
    elif sev == 'High':
        return 3
    elif sev == 'Important':
        return 3
    elif sev == 'Medium':
        return 2
    elif sev == 'Low':
        return 1
    return 0


def camel_case_transformer(s):
    """
    Converts a camel case string into space separated words starting with a capital letters
    E.g. input: 'camelCase' output: 'Camel Case'
    REMARK: the exceptions list below is returned uppercase, e.g. "cve" => "CVE"
    """

    transformed_string = re.sub('([a-z])([A-Z])', r'\g<1> \g<2>', str(s))
    if transformed_string in ['id', 'cve', 'arn']:
        return transformed_string.upper()
    return transformed_string.title()


def get_headers(name: str, data: list) -> list:
    """
    Returns a list of headers to the given list of objects
    If the list name is known (listed in the HEADERS_BY_NAME) it returns the list and checks for any additional headers
     in the given list
    Else returns the given headers from the given list
    Args:
        name: name of the list (e.g. vulnerabilities)
        data: list of dicts

    Returns: list of headers
    """

    # check the list for any additional headers that might have been added
    known_headers = HEADERS_BY_NAME.get(name)
    if known_headers:
        headers = known_headers[:]
    else:
        headers = []

    if isinstance(data, list):
        for d in data:
            if isinstance(d, dict):
                for key in d.keys():
                    if key not in headers:
                        headers.append(key)
    return headers


def test_module(client):
    """
    Test connection, authentication and user authorization
    Args:
        client: Requests client
    Returns:
        'ok' if test passed, error from client otherwise
    """

    client.test()
    return 'ok'


def fetch_incidents(client):
    """
    Fetches new alerts from Prisma Cloud Compute and returns them as a list of Demisto incidents
    - A markdown table will be added for alerts with a list object,
      If the alert has a list under field "tableField", another field will be added to the
      incident "tableFieldMarkdownTable" representing the markdown table
    Args:
        client: Prisma Compute client
    Returns:
        list of incidents
    """
    incidents = []
    alerts = client.list_incidents()

    if alerts:
        for a in alerts:
            alert_type = a.get('kind')
            name = ALERT_TITLE
            severity = 0

            # fix the audit category from camel case to display properly
            if alert_type == ALERT_TYPE_AUDIT:
                a['category'] = camel_case_transformer(a.get('category'))

            # always save the raw JSON data under this argument (used in scripts)
            a['rawJSONAlert'] = json.dumps(a)

            # parse any list into a markdown table, since tableToMarkdown takes the headers from the first object in
            # the list check headers manually since some entries might have omit empty fields
            tables = {}
            for key, value in a.items():
                # check only if we got a non empty list of dict
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    tables[key + 'MarkdownTable'] = tableToMarkdown(camel_case_transformer(key + ' table'),
                                                                    value,
                                                                    headers=get_headers(key, value),
                                                                    headerTransform=camel_case_transformer,
                                                                    removeNull=True)

            a.update(tables)

            if alert_type == ALERT_TYPE_VULNERABILITY:
                # E.g. "Prisma Cloud Compute Alert - imageName Vulnerabilities"
                name += a.get('imageName') + ' Vulnerabilities'
                # Set the severity to the highest vulnerability, take the first from the list
                severity = translate_severity(a.get('vulnerabilities')[0].get('severity'))

            elif alert_type == ALERT_TYPE_COMPLIANCE or alert_type == ALERT_TYPE_AUDIT:
                # E.g. "Prisma Cloud Compute Alert - Incident"
                name += camel_case_transformer(a.get('type'))
                # E.g. "Prisma Cloud Compute Alert - Image Compliance" \ "Prisma Compute Alert - Host Runtime Audit"
                if a.get('type') != "incident":
                    name += ' ' + camel_case_transformer(alert_type)

            else:
                # E.g. "Prisma Cloud Compute Alert - Cloud Discovery"
                name += camel_case_transformer(alert_type)

            incidents.append({
                'name': name,
                'occurred': a.get('time'),
                'severity': severity,
                'rawJSON': json.dumps(a)
            })

    return incidents


def parse_limit_and_offset_values(limit: str, offset: str) -> dict:
    """
    Parse the offset and limit parameters.

    Args:
        limit (str): api request limit.
        offset (str): api request offset.

    Returns:
        dict: parsed offset and limit as integers.
    """

    offset, limit = arg_to_number(arg=offset, arg_name="offset"), arg_to_number(arg=limit, arg_name="limit")

    if offset is not None and offset < 0:
        raise ValueError(f"offset parameter {offset} is invalid, cannot be a negative number")

    if limit is not None and (limit < 1 or limit > MAX_API_LIMIT):
        raise ValueError(f"limit parameter '{limit}' is invalid, must be between 1-50")

    return {
        "offset": offset,
        "limit": limit
    }


def update_query_params_names(names: List[Tuple[str, str]], args: dict) -> None:
    """
    Update the query parameters names.

    Args:
        names (list): a list of old name and new names to replace.
        args (dict): a dict to replace its old key names with new key names.
    """
    for old_name, new_name in names:
        if old_name in args:
            args[new_name] = args.pop(old_name)


def parse_date_string_format(date_string: str, new_format: str = "%B %d, %Y %H:%M:%S %p") -> str:
    """
    Parses a date string format to a different date string format.

    Args:
        date_string (str): the date in string representation.
        new_format (str): the new requested format for the date string.

    Returns:
        str: date as a new format, in case of a failure returns the original date string.
    """
    try:
        return dateparser.parse(date_string=date_string).strftime(new_format)
    except AttributeError:
        return date_string


def get_api_filtered_response(
    client: PrismaCloudComputeClient,
    url_suffix: str,
    offset: int,
    limit: int,
    args: Optional[dict] = None,
) -> list:
    """
    Filter the api response according to the offset/limit, used in case the api doesn't support limit/offset

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        url_suffix (str): url suffix of the base api url.
        offset (int): the offset from which to begin listing the response.
        limit (int): the maximum limit of records in the response to fetch.
        args (dict): any command arguments if exist.

    Returns:
        list: api filtered response, empty list in case there aren't any records in the api response.
    """
    if not args:
        args = {}

    response = client.api_request(method='GET', url_suffix=url_suffix, params=assign_params(**args))

    if response:
        start = min(offset, len(response))
        end = min(offset + limit, len(response))
        return response[start:end]

    return []


def build_single_host_profile_table_response(host_info: dict) -> str:
    """
    Build a table for a single host.

    Args:
        host_info (dict): host information from the api.

    Returns:
        str: markdown table output for a single host.
    """
    host_description_table = build_hostnames_description_table(
        host_description_info=get_hostname_description_info(host_info=host_info)
    )

    apps_info = [
        {
            'HostId': host_info.get('_id'),
            'AppName': app.get('name'),
            'StartupProcess': app.get('startupProcess').get('path'),
            'User': app.get('startupProcess').get('user'),
            'LaunchTime': parse_date_string_format(date_string=app.get('startupProcess').get('time'))
        } for app in host_info.get('apps', [])
    ]
    ssh_events_info = [
        {
            'User': event.get('user'),
            'Ip': str(ipaddress.IPv4Address(event.get('ip'))),
            'ProcessPath': event.get('path'),
            'Command': event.get('command'),
            'Time': parse_date_string_format(date_string=event.get('time'))
        } for event in host_info.get('sshEvents', [])
    ]

    apps_table = tableToMarkdown(
        name='Apps',
        t=apps_info,
        headers=['AppName', 'StartupProcess', 'User', 'LaunchTime'],
        removeNull=True
    )
    ssh_events_table = tableToMarkdown(
        name='SSH Events',
        t=ssh_events_info,
        headers=['User', 'Ip', 'ProcessPath', 'Command', 'Time'],
        removeNull=True
    )

    return host_description_table + apps_table + ssh_events_table


def get_hostname_description_info(host_info: dict) -> dict:
    """
    Get the hostname description information.

    Args:
        host_info (dict): host's information from the api.

    Returns:
        dict: host description information
    """
    dist = host_info["labels"][0].replace("osDistro:", "") + " " + host_info["labels"][1].replace("osVersion:", "")

    return {
        "Hostname": host_info.get("_id"),
        "Distribution": dist,
        "Collections": host_info.get("collections")
    }


def build_hostnames_description_table(host_description_info: Union[List[dict], dict]) -> str:
    """
    Build the hostname description table.

    Args:
        host_description_info (dict/list): hosts description information.

    Returns:
        str: markdown table that describes the host/s.
    """
    return tableToMarkdown(
        name="Host Description",
        t=host_description_info,
        headers=["Hostname", "Distribution", "Collections"],
        removeNull=True
    )


def build_profile_host_table_response(hosts_info: List[dict]) -> str:
    """
    Build a table from the api response of the profile host
    list for the command 'prisma-cloud-compute-profile-host-list'

    Args:
        hosts_info (list[dict]): the api raw response.

    Returns:
        str: markdown table output for the apps and ssh events of a host.
    """
    if not hosts_info:
        return "No results found"

    if len(hosts_info) == 1:  # then we have only one host
        return build_single_host_profile_table_response(host_info=hosts_info[0])

    return build_hostnames_description_table(
        host_description_info=[get_hostname_description_info(host_info=host_info) for host_info in hosts_info]
    )


def update_host_profile_context_fields(hosts_profile_info: List[dict]):
    """
    Update the fields for the context output of the 'prisma-cloud-compute-profile-host-list' command.

    Args:
        hosts_profile_info (list[dict]): hosts profile api response
    """
    for host_profile in hosts_profile_info:
        if "created" in host_profile:
            host_profile["created"] = parse_date_string_format(date_string=host_profile.get("created"))
            host_profile["time"] = parse_date_string_format(date_string=host_profile.get("time"))
        for event in host_profile.get("sshEvents", []):
            if "ip" in event:
                event["ip"] = str(ipaddress.IPv4Address(event.get("ip")))


def get_profile_host_list(client: PrismaCloudComputeClient, args: dict) -> CommandResults:
    """
    Get information about the hosts and their profile events.
    Implement the command 'prisma-cloud-compute-profile-host-list'

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        args (dict): prisma-cloud-compute-profile-host-list command arguments.

    Returns:
        CommandResults: command-results object.
    """
    update_query_params_names(names=[("hostname", "hostName")], args=args)
    args.update(parse_limit_and_offset_values(limit=args.get("limit", "15"), offset=args.get("offset", "0")))

    hosts_profile_info = client.api_request(
        method='GET', url_suffix='/profiles/host', params=assign_params(**args)
    )

    update_host_profile_context_fields(hosts_profile_info=hosts_profile_info)   # type:ignore

    return CommandResults(
        outputs_prefix='PrismaCloudCompute.ProfileHost',
        outputs_key_field='_id',
        outputs=hosts_profile_info,
        readable_output=build_profile_host_table_response(hosts_info=hosts_profile_info),  # type:ignore
        raw_response=hosts_profile_info
    )


def build_single_container_profile_table(container_info: dict):
    """
    Build a table for a single container.

    Args:
        container_info (dict): container information from the api.

    Returns:
        str: markdown table output for a single container.
    """
    container_description_table = build_containers_description_table(
        container_description=get_container_description_info(container_info=container_info)
    )

    processes_info = [
        {
            "Type": process_type,
            "Path": static_process.get("path"),
            "DetectionTime": parse_date_string_format(date_string=static_process.get("time"))
        } for process_type in ["static", "behavioral"]
        for static_process in container_info.get("processes", {}).get(process_type, "")
    ]

    processes_table = tableToMarkdown(
        name='Processes',
        t=processes_info,
        headers=['Type', 'Path', 'DetectionTime'],
        removeNull=True
    )

    return container_description_table + processes_table


def build_containers_description_table(container_description: Union[List[dict], dict]) -> str:
    """
    Build the container description table.

    Args:
        container_description (dict/list): containers description information.

    Returns:
        str: markdown table that describes the container/s.
    """
    return tableToMarkdown(
        name="Container Description",
        t=container_description,
        headers=["ContainerID", "Image", "Os", "State", "Created"],
        removeNull=True
    )


def get_container_description_info(container_info):
    """
    Build a table for a single host.

    Args:
        container_info (dict): container information from the api.

    Returns:
        dict: container description information.
    """
    return {
        "ContainerID": container_info.get("_id"),
        "Image": container_info.get("image"),
        "Os": container_info.get("os"),
        "State": container_info.get("state"),
        "Created": parse_date_string_format(date_string=container_info.get("created", ""))
    }


def build_profile_container_table_response(containers_info: List[dict]) -> str:
    """
    Build a table from the api response of the profile container
    list for the command 'prisma-cloud-compute-profile-container-list'

    Args:
        containers_info (list[dict]): the api raw response.

    Returns:
        str: markdown table output.
    """
    if not containers_info:
        return "No results found"

    if len(containers_info) == 1:  # means we have only one container
        return build_single_container_profile_table(container_info=containers_info[0])

    return build_containers_description_table(
        container_description=[
            get_container_description_info(container_info=container_info) for container_info in containers_info
        ]
    )


def get_container_profile_list(client: PrismaCloudComputeClient, args: dict) -> CommandResults:
    """
    Get information about the containers and their profile events.
    Implement the command 'prisma-cloud-compute-profile-container-list'

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        args (dict): prisma-cloud-compute-profile-container-list command arguments.

    Returns:
        CommandResults: command-results object.
    """
    update_query_params_names(names=[("image_id", "imageID")], args=args)
    args.update(parse_limit_and_offset_values(limit=args.get("limit", "15"), offset=args.get("offset", "0")))

    containers_info = client.api_request(
        method='GET', params=assign_params(**args), url_suffix='/profiles/container'
    )

    return CommandResults(
        outputs_prefix='PrismaCloudCompute.ProfileContainer',
        outputs_key_field='_id',
        outputs=containers_info,
        readable_output=build_profile_container_table_response(containers_info=containers_info),  # type:ignore
        raw_response=containers_info
    )


def build_container_hosts_response(
    client: PrismaCloudComputeClient, container_id: str, args: dict
) -> Tuple[Optional[dict], str]:
    """
    Build a table and a context response for the 'prisma-cloud-compute-profile-container-hosts-list' command.

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        container_id (str): container ID.
        args (dict): prisma-cloud-compute-profile-container-list command arguments.

    Returns:
        Tuple[dict, str]: Context and table response.
    """
    # this api endpoint does not support either limit/offset.
    limit, offset = args.pop("limit"), args.pop("offset")

    hosts_ids = get_api_filtered_response(
        client=client,
        url_suffix=f"profiles/container/{container_id}/hosts",
        offset=offset,
        limit=limit,
        args=args,
    )

    if hosts_ids:
        context_output = {
            "containerID": container_id,
            "hostsIDs": hosts_ids
        }
        return context_output, tableToMarkdown(
            name="Containers hosts list",
            t=context_output,
            headers=["containerID", "hostsIDs"],
            headerTransform=lambda word: word[0].upper() + word[1:]
        )
    return None, "No results found"


def get_container_hosts_list(client: PrismaCloudComputeClient, args: dict) -> CommandResults:
    """
    Returns the hosts where the containers are running.
    Implement the command 'prisma-cloud-compute-profile-container-hosts-list'.

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        args (dict): prisma-cloud-compute-profile-container-hosts-list command arguments.

    Returns:
        CommandResults: command-results object.
    """
    container_id = args.pop("id")
    args.update(parse_limit_and_offset_values(limit=args.get("limit", "50"), offset=args.get("offset", "0")))

    context, table = build_container_hosts_response(client=client, container_id=container_id, args=args)

    return CommandResults(
        outputs_prefix="PrismaCloudCompute.ProfileContainerHost",
        outputs=context,
        readable_output=table,
        outputs_key_field="ContainerID"
    )


def build_containers_forensic_response(
    client: PrismaCloudComputeClient, container_id: str, args: dict
) -> Tuple[Optional[dict], str]:
    """
    Build a table and a context response for the 'prisma-cloud-compute-profile-container-forensic-list' command.

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        container_id (str): container ID.
        args (dict): prisma-cloud-compute-profile-container-forensic-list command arguments.

    Returns:
        Tuple[dict, str]: Context and table response.
    """
    # api request does not support offset only, but does support limit.
    offset = args.pop("offset", 0)
    # because the api supports only limit, it is necessary to add the requested offset to the limit be able to take the
    # correct offset:limit after the api call.
    limit = args.get("limit", 20)
    args["limit"] = offset + args["limit"]

    container_forensics = get_api_filtered_response(
        client=client, url_suffix=f"profiles/container/{container_id}/forensic", offset=offset, limit=limit, args=args
    )

    if container_forensics:
        context_output = {
            "ContainerID": container_id,
            "Hostname": args.get("hostname"),
            "Forensics": container_forensics
        }

        return context_output, tableToMarkdown(
            name="Containers forensic report",
            t=context_output["Forensics"],
            headers=["ContainerID", "Type", "Path", "User", "Pid"],
            removeNull=True
        )
    return None, "No results found"


def get_profile_container_forensic_list(client: PrismaCloudComputeClient, args: dict) -> CommandResults:
    """
    Returns runtime forensics data for a specific container on a specific host.
    Implement the command 'prisma-cloud-compute-profile-container-forensic-list'

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        args (dict): prisma-cloud-compute-profile-container-forensic-list command arguments.

    Returns:
        CommandResults: command-results object.
    """
    container_id = args.pop("id")
    update_query_params_names(names=[("incident_id", "incidentID")], args=args)
    args.update(parse_limit_and_offset_values(limit=args.get("limit", "20"), offset=args.get("offset", "0")))

    context, table = build_containers_forensic_response(
        client=client, container_id=container_id, args=args
    )

    return CommandResults(
        outputs_prefix='PrismaCloudCompute.ContainerForensic',
        outputs=context,
        readable_output=table,
        outputs_key_field=["ContainerID", "Hostname"]
    )


def build_host_forensic_response(
    client: PrismaCloudComputeClient, host_id: str, args: dict
) -> Tuple[Optional[dict], str]:
    """
    Build a table and a context response for the 'prisma-cloud-compute-host-forensic-list' command.

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        host_id (str): host ID.
        args (dict): prisma-cloud-compute-profile-container-forensic-list command arguments.

    Returns:
        Tuple[dict, str]: Context and table response.
    """
    # api request does not support offset only, but does support limit.
    offset = args.pop("offset", 0)
    limit = args.get("limit", 20)
    # because the api supports only limit, it is necessary to add the requested offset to the limit be able to take the
    # correct offset:limit after the api call.
    args["limit"] = offset + args["limit"]

    host_forensics = get_api_filtered_response(
        client=client, url_suffix=f"/profiles/host/{host_id}/forensic", offset=offset, limit=limit, args=args
    )

    if host_forensics:
        context_output = {
            "HostID": host_id,
            "Forensics": host_forensics
        }

        return context_output, tableToMarkdown(
            name="Host forensics report", t=host_forensics, headers=["Type", "App", "Path", "Command"], removeNull=True
        )
    return None, "No results found"


def get_profile_host_forensic_list(client: PrismaCloudComputeClient, args: dict) -> CommandResults:
    """
    Returns runtime forensics data for a specific host.
    Implement the command 'prisma-cloud-compute-host-forensic-list'

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        args (dict): prisma-cloud-compute-host-forensic-list command arguments.

    Returns:
        CommandResults: command-results object.
    """

    host_id = args.pop("id")
    update_query_params_names(names=[("incident_id", "incidentID")], args=args)
    args.update(parse_limit_and_offset_values(limit=args.get("limit", "20"), offset=args.get("offset", "0")))

    context, table = build_host_forensic_response(client=client, host_id=host_id, args=args)

    return CommandResults(
        outputs_prefix='PrismaCloudCompute.HostForensic',
        outputs=context,
        readable_output=table,
        outputs_key_field="HostID"
    )


def get_console_version(client: PrismaCloudComputeClient) -> CommandResults:
    """
    Returns the version of the prisma cloud compute console.
    Implement the command 'prisma-cloud-compute-console-version-info'.

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.

    Returns:
        CommandResults: command-results object.
    """
    version = client.api_request(method="GET", url_suffix="/version")

    return CommandResults(
        outputs_prefix="PrismaCloudCompute.Console.Version",
        outputs=version,
        readable_output=tableToMarkdown(name="Console version", t={"Version": version}, headers=["Version"])
    )


def get_custom_feeds_ip_list(client: PrismaCloudComputeClient) -> CommandResults:
    """
    Get all the BlackListed IP addresses in the system.
    Implement the command 'prisma-cloud-compute-custom-feeds-ip-list'

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.

    Returns:
        CommandResults: command-results object.
    """
    feeds = client.api_request(method='GET', url_suffix="/feeds/custom/ips")

    if feeds:
        feeds = capitalize_api_response(api_response=feeds)
        if "Modified" in feeds:
            feeds["Modified"] = parse_date_string_format(date_string=feeds.get("Modified", ""))
        table = tableToMarkdown(name="IP Feeds", t=feeds, headers=["Modified", "Feed"])
    else:
        table = "No results found"

    return CommandResults(
        outputs_prefix="PrismaCloudCompute.CustomFeedIP",
        outputs=feeds,
        readable_output=table,
        outputs_key_field="Digest"
    )


def add_custom_ip_feeds(client: PrismaCloudComputeClient, args: dict) -> CommandResults:
    """
    Add a list of banned IPs to be blocked by the system.
    Implement the command 'prisma-cloud-compute-custom-feeds-ip-add'

    Args:
        client (PrismaCloudComputeClient): prisma-cloud-compute client.
        args (dict): prisma-cloud-compute-custom-feeds-ip-add command arguments.

    Returns:
        CommandResults: command-results object.
    """
    # the api overrides the blacklisted IPs, therefore it is necessary to add those who exist to the 'PUT' request.
    current_ip_feeds = client.api_request(method="GET", url_suffix="/feeds/custom/ips").get("feed", [])
    new_ip_feeds = argToList(arg=args.pop("ip"))

    # remove duplicates, the api doesn't give error on duplicate IPs
    combined_feeds = list(set(current_ip_feeds + new_ip_feeds))

    client.api_request(
        url_suffix="/feeds/custom/ips",
        method='PUT',
        json_data={"feed": combined_feeds}
    )

    return CommandResults(
        readable_output=tableToMarkdown(name="Successfully updated the custom IP feeds", t=[])
    )


def main():
    """
    PARSE AND VALIDATE INTEGRATION PARAMS
    """
    params = demisto.params()
    username = params.get('credentials').get('identifier')
    password = params.get('credentials').get('password')
    base_url = params.get('address')
    project = params.get('project', '')
    verify_certificate = not params.get('insecure', False)
    cert = params.get('certificate')
    proxy = params.get('proxy', False)

    # If checked to verify and given a certificate, save the certificate as a temp file
    # and set the path to the requests client
    if verify_certificate and cert:
        tmp = tempfile.NamedTemporaryFile(delete=False, mode='w')
        tmp.write(cert)
        tmp.close()
        verify = tmp.name
    else:
        # Save boolean as a string
        verify = str(verify_certificate)

    try:
        requested_command = demisto.command()
        LOG(f'Command being called is {requested_command}')

        # Init the client
        client = PrismaCloudComputeClient(
            base_url=urljoin(base_url, 'api/v1/'),
            verify=verify,
            auth=(username, password),
            proxy=proxy,
            project=project
        )

        if requested_command == 'test-module':
            # This is the call made when pressing the integration test button
            result = test_module(client)
            demisto.results(result)

        elif requested_command == 'fetch-incidents':
            # Fetch incidents from Prisma Cloud Compute
            # this method is called periodically when 'fetch incidents' is checked
            incidents = fetch_incidents(client)
            demisto.incidents(incidents)
        elif requested_command == 'prisma-cloud-compute-profile-host-list':
            return_results(results=get_profile_host_list(client=client, args=demisto.args()))
        elif requested_command == 'prisma-cloud-compute-profile-container-list':
            return_results(results=get_container_profile_list(client=client, args=demisto.args()))
        elif requested_command == 'prisma-cloud-compute-profile-container-hosts-list':
            return_results(results=get_container_hosts_list(client=client, args=demisto.args()))
        elif requested_command == 'prisma-cloud-compute-profile-container-forensic-list':
            return_results(results=get_profile_container_forensic_list(client=client, args=demisto.args()))
        elif requested_command == 'prisma-cloud-compute-host-forensic-list':
            return_results(results=get_profile_host_forensic_list(client=client, args=demisto.args()))
        elif requested_command == 'prisma-cloud-compute-custom-feeds-ip-add':
            return_results(results=add_custom_ip_feeds(client=client, args=demisto.args()))
        elif requested_command == 'prisma-cloud-compute-console-version-info':
            return_results(results=get_console_version(client=client))
        elif requested_command == 'prisma-cloud-compute-custom-feeds-ip-list':
            return_results(results=get_custom_feeds_ip_list(client=client))
    # Log exceptions
    except Exception as e:
        return_error(f'Failed to execute {requested_command} command. Error: {str(e)}')


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
