FROM node:22-alpine AS builder
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-workspace.yaml tsconfig.base.json ./
COPY packages/shared/package.json packages/shared/
COPY packages/ui/package.json packages/ui/
COPY apps/web/package.json apps/web/
RUN pnpm install --no-frozen-lockfile
COPY tsconfig.base.json ./
COPY packages ./packages
COPY apps/web ./apps/web
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_OUTPUT=standalone
RUN pnpm --filter @cloudpartnerops/web build

FROM node:22-alpine AS runner
RUN corepack enable
WORKDIR /repo
# Full-workspace copy: reliable in Phase 0. Layer-pruning for production
# images is scheduled in Phase 6 (deployment hardening).
COPY --from=builder /repo ./
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
EXPOSE 3000
WORKDIR /repo/apps/web
CMD ["pnpm", "start"]
