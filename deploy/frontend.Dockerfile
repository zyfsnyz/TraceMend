FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/eslint.config.js frontend/postcss.config.js frontend/tailwind.config.js ./
COPY frontend/src ./src
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
