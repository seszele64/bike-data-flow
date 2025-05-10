#!/bin/bash

# Directory where you want to save the content. Make sure this directory exists.
DIRECTORY="/box/data-scrape/wrm/gen_info/"

# File name with date and time
# Note: date -d '+1 hour' adjusts the time from GMT to GMT+1
FILENAME="content_$(TZ='GMT+1' date +'%Y-%m-%d_%H-%M-%S').txt"

# The URL to download
URL="https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"

# Use curl to download the content and save it to the specified directory
curl "$URL" -o "$DIRECTORY/$FILENAME"
