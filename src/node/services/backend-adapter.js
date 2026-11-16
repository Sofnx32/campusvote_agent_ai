import { ActorResolver } from './actor-resolver.js';
import { SiteVerifier } from './site-verifier.js';
import { FairVerifier } from './fair-verifier.js';
import { ToolExecutor } from './tool-executor.js';

export class BackendAdapter {
  constructor(fetchImpl = fetch) {
    this.actorResolver = new ActorResolver(fetchImpl);
    this.siteVerifier = new SiteVerifier(fetchImpl);
    this.fairVerifier = new FairVerifier(fetchImpl);
    this.toolExecutor = new ToolExecutor(fetchImpl);
  }

  async resolveActorContext(userToken, signal, correlationId) {
    return this.actorResolver.resolve(userToken, signal, correlationId);
  }

  async assertActiveAssignment(context, siteId, userToken, signal) {
    return this.siteVerifier.assertActiveAssignment(context, siteId, userToken, signal);
  }

  async callTool(tool, input, context, userToken, signal) {
    return this.toolExecutor.callTool(tool, input, context, userToken, signal, this.siteVerifier, this.fairVerifier);
  }

  async assertFairAssignment(fairId, siteId, context, userToken, signal) {
    return this.fairVerifier.assertFairAssignment(fairId, siteId, context, userToken, signal);
  }
}