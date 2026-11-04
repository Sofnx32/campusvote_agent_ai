FROM node:22-bookworm-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY src/node ./src/node
EXPOSE 3000
CMD ["node", "src/node/server.js"]
