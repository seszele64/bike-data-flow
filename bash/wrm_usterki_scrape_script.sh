#!/bin/bash

# Directory where you want to save the content. Make sure this directory exists.
DIRECTORY="/box/data-scrape/wrm/usterki/"

# File name with date and time
FILENAME="wrm_usterki_$(date +'%Y-%m-%d').csv"

# The URL from which to download the CSV file
URL="https://www.wroclaw.pl/open-data/39605eb0-8055-4733-bb02-04b96791d36a/WRM_usterki.csv"

# Use curl to download the CSV file and save it to the specified directory
curl "$URL" -o "$DIRECTORY/$FILENAME"
