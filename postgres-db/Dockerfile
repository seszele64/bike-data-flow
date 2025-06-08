FROM postgres:latest

# These will be set by docker-compose or docker run using .env file
# No need to hardcode them in the Dockerfile
COPY init.sql /docker-entrypoint-initdb.d/ 

EXPOSE 5432