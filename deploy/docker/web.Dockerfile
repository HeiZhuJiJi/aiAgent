FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /app
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web/ ./
RUN pnpm build

FROM node:22-alpine
ENV PORT=8080 API_UPSTREAM=http://api:8080
WORKDIR /app
COPY deploy/docker/static-server.mjs /app/server.mjs
COPY --from=build /app/dist /app/public
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=12 CMD node -e "fetch('http://127.0.0.1:8080/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "/app/server.mjs"]
