FROM node:18-alpine
WORKDIR /usr/src
COPY ncacademy_website/app/package*.json ./
RUN npm install

COPY ncacademy_website/app/ ./

RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
