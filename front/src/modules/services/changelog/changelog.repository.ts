import type { ChangelogEntry, IChangelogRepository } from '@/types'

export default class ChangelogRepository implements IChangelogRepository {
  public getEntries(): ChangelogEntry[] {
    return [
      {
        version: '0.9.9',
        date: '2026-08-28',
        items: []
      }
    ]
  }
}
