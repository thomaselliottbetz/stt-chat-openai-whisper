FROM public.ecr.aws/lambda/python:3.12

# Install the specified packages
RUN dnf install -y git

# Install git (and any other dependencies)
RUN pip install git+https://github.com/openai/whisper.git

# Set environment variable to use this directory for Whisper
ENV WHISPER_MODEL_DIR="/opt/models"

# Copy the pre-compiled Linux ffmpeg binary into /usr/local/bin
COPY ffmpeg /usr/local/bin/ffmpeg

# Ensure the binary is executable
RUN chmod +x /usr/local/bin/ffmpeg


# Copy the pre-trained Whisper model directly into the container
RUN mkdir -p /opt/models
RUN python3 -c "import whisper; whisper.load_model('small', download_root='/opt/models')"

# Copy function code
COPY main.py ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
CMD [ "main.handler" ]