FROM node:22-alpine AS build
WORKDIR /app

ARG VITE_ORCHESTRATOR_BASE_URL=http://localhost:8000
ENV VITE_ORCHESTRATOR_BASE_URL=${VITE_ORCHESTRATOR_BASE_URL}

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
WORKDIR /usr/share/nginx/html
COPY --from=build /app/dist .
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
