FROM node:18-alpine
WORKDIR /
COPY ncacademy_website/package*.json ./
RUN npm install

COPY ncacademy_website/ ./

RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
