FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=warn
COPY . .
EXPOSE 3001
CMD ["npm", "run", "dev"]