FROM python:3.6-stretch
MAINTAINER Ayrton San Joaquin <ajsanjoaquin@gmail.com>

# check our python environment
RUN python3 --version
RUN pip3 --version

# set work directory for the container
WORKDIR  /usr/src/covid_triage
#install reqs
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
#get model weights
RUN wget -O corona_resnet34.pth https://www.dropbox.com/s/o27w0dik8hdjaab/corona_resnet34.pth?dl=0

COPY . /src/

# Running Python Application
CMD ["python3", "app.py"]