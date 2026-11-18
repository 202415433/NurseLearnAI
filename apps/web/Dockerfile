# Interface web NurseLearn AI (Next.js, sortie « standalone »).
# Contexte de construction : racine du dépôt (voir infra/docker-compose.yml).
FROM node:22-alpine AS build
WORKDIR /repo
COPY packages/design-tokens ./packages/design-tokens
COPY apps/web/package.json apps/web/package-lock.json* ./apps/web/
WORKDIR /repo/apps/web
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY apps/web ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
RUN addgroup -S -g 10001 nurselearn && adduser -S -u 10001 -G nurselearn nurselearn
COPY --from=build --chown=nurselearn /repo/apps/web/.next/standalone ./
COPY --from=build --chown=nurselearn /repo/apps/web/.next/static ./.next/static
COPY --from=build --chown=nurselearn /repo/apps/web/public ./public
USER nurselearn
EXPOSE 3000
CMD ["node", "server.js"]
