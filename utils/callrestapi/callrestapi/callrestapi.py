import requests
import pandas as pd


def get_rest_api(
    url: str,
    key: str,
    auth_key: str,
    format: str,
    params: dict = None,
    element_id: str = None,
    csv_file_prefix: str = None,
) -> pd.DataFrame:
    """
    Calls a REST API endpoint with the appropriate parameters, handles any errors that may occur during the request,
    and returns the results as a pandas data frame.

    Parameters:
    -url: The URL of the REST API endpoint.
    -key: The subscription key required for authentication.
    -auth_key: The basic authentication credentials.
    -format: The format in which the request URLs provide the response. REST endpoints return differently formatted JSONs and/or binary (CSV) files.
    -params: A dictionary that may contain additional parameters to pass to the API endpoint. By default, it is set to None
    -element_id: Name of an element to include in the name of a CSV file. By default, it is set to None.
    -csv_file_prefix: Prefix to include as part of the CSV file name. By default, it is set to None.

    Returns:
    -df: The results as a pandas data frame.
    """
    # Create the request headers
    hdr = {
        "Cache-Control": "no-cache",
        "Ocp-Apim-Subscription-Key": key,
        "Authorization": auth_key,
    }

    try:
        # Make the GET request to the API endpoint
        if params:
            request = requests.get(url, headers=hdr, params=params)
        else:
            request = requests.get(url, headers=hdr)

        # Check the response status code
        if request.status_code == 200:
            # Process the response based on the specified format
            if format == "json_format":
                # return a data frame with json objects
                df = pd.json_normalize(request.json())
            elif format == "csv_download":
                with open(
                    element_id + "_" + csv_file_prefix + "_csv_output.csv", "wb"
                ) as file:
                    file.write(request.content)
                    print("CSV file downloaded successfully")
                # Read the CSV file into a Pandas data frame
                print("Below is a display of the CSV file")
                df = pd.read_csv(element_id + "_" + csv_file_prefix + "_csv_output.csv")
            else:
                print(
                    "This is not the correct format. Possible formats are: json_format, and csv_download"
                )
                df = None
        else:
            print(f"Failed to download. Server response: {request.status_code}")
            print(
                "Response content:", request.text
            )  # Print the response content for more details
            df = None

    except requests.exceptions.RequestException as exceptions:
        print(f"An error occurred while making the HTTP request: {exceptions}")
        df = None
    except Exception as exceptions:
        print(f"An unexpected error occurred: {exceptions}")
        df = None

    return df


def unpack_json(
    df: pd.DataFrame, list_name: str, column_names: list = None
) -> pd.DataFrame:
    """
    Unpacks the list of dictionaries returned by the `get_rest_api` function when used with the `json_format` option.
    Optionally display additional `column_name` in a data frame. Returns the results in a data frame.

    Parameters:
    - df: A data frame containing the list of dictionaries.
    - list_name: The name of the column containing the list of dictionaries.
    - column_names: A list of additional columns to display. The default is None.

    Return:
    -df_unpacked: The results as a pandas data frame.
    """

    # Create an empty list that will store the results.
    unpacked_dicts = []

    for _, row in df.iterrows():

        # Unpack the dictionary in the current row.
        unpack = pd.json_normalize(row[list_name])

        if column_names is not None:
            # Loop over the list of columns to be displayed.
            for column in column_names:
                unpack[column] = row[column]

        # Append the unpacked dictionary to the list.
        unpacked_dicts.append(unpack)

    # Concatenate all the unpacked dictionaries into a single data frame.
    df_unpacked = pd.concat(unpacked_dicts, ignore_index=True)

    return df_unpacked
